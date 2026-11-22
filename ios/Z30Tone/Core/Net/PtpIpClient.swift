import Darwin
import Foundation

/// Wi-Fi 直连相机时的 PTP/IP 客户端。
///
/// 协议来自对官方 Wireless Transmitter Utility 的抓包（Z30 固件 V1.20）：
///   帧结构 = 4 字节小端总长度（含这 4 字节）+ 包体
///   Init 请求包体 56 字节 = 类型 4 + 连接号 4 + 主机 ID 12 + UTF-16 名 + NUL + 版本 4
///   Init 应答包体 54 字节 = 类型 4 + 连接号 4 + 相机 ID 16 + UTF-16 名 + NUL + 版本 4
///   操作请求  = 类型 6 + DataPhaseInfo 4 + 操作码 2 + 事务号 4 + 参数 4*n
///   操作应答  = 类型 7 + 操作码 2 + 事务号 4 + 参数 4*n
///   数据阶段  = StartData 9 / Data 10 / EndData 12，均带 4 字节事务号
///
/// 主机 ID 是相机「记住」的凭据。实测结论（Z30 V1.20）：
/// 相机新建的网络配置文件处于「未认领」状态时，任意主机都能握手并读写；
/// 一旦被某台主机连过，后续只认那一个 ID，其他 ID 会收到 Init_Fail。
/// 因此本类使用「首次启动生成并持久化」的随机身份，而不是写死某个值。
///
/// 槽位编号与 USB 不同：Wi-Fi 下 0xC9–0xCF 返回 36 字节的内置影调描述符，
/// 真正的 614 字节自定义载荷从 0xD0 起。所以这里不硬编码，而是扫描后按顺序
/// 映射成 C1–C9。
enum PtpIpError: LocalizedError {
    case socket(String)
    case timeout
    case handshake(String)
    case camera(code: UInt16)

    var errorDescription: String? {
        switch self {
        case .socket(let message):
            return "网络错误：\(message)"
        case .timeout:
            return "相机没有响应（超时）。确认相机没熄屏、且仍停在「连接到计算机」"
        case .handshake(let message):
            return "握手失败：\(message)"
        case .camera(let code):
            return "相机返回错误 0x" + String(format: "%04X", code) + "（\(PtpIpClient.responseText(code))）"
        }
    }
}

/// 相机上的一个自定义槽位。
struct CameraSlot: Identifiable, Hashable {
    /// 相机侧的槽位编号（0xC9 这种）。
    let id: Int
    /// 界面上的名字，C1…C9。
    let label: String
    /// 载荷里的名称字段。
    let name: String
    let lutFirst: Int
    let lutLast: Int
}

/// 设备信息里我们关心的几项。
struct CameraInfo {
    let model: String
    let firmware: String
    let serial: String
    let supportsRead: Bool
    let supportsWrite: Bool
}

final class PtpIpClient: @unchecked Sendable {

    static let defaultHost = "192.168.1.1"
    static let defaultPort: UInt16 = 15740

    /// 本机身份：12 字节主机 ID + 主机名。首次启动时随机生成并持久化。
    ///
    /// 实测（Z30 V1.20）：相机新建的网络配置文件处于「未认领」状态时，
    /// **任意主机都能握手成功并读写槽位**；一旦被某台主机连过，它只认那一个 ID。
    /// 所以身份必须在安装时生成一次并固定下来，不能每次连接都换。
    private let hostID: Data
    private let hostName: String

    init(hostID: Data, hostName: String) {
        self.hostID = hostID
        self.hostName = hostName
    }

    /// 按实测格式拼 Init Command Request 包体（56 字节）。
    /// 格式：类型4(=1) + 连接号4 + 主机ID12 + UTF-16名 + NUL + 版本4
    private static func makeInitBody(hostID: Data, hostName: String, conn: UInt32) -> Data {
        var body = Data()
        body.append(le32(1))
        body.append(le32(conn))
        body.append(hostID.prefix(12))
        for unit in hostName.utf16 {
            body.append(UInt8(unit & 0xFF))
            body.append(UInt8((unit >> 8) & 0xFF))
        }
        body.append(contentsOf: [0x00, 0x00])
        body.append(le32(0x0001_0000))
        return body
    }

    /// 生成一个随机身份。主机名保持 ASCII，避免相机端显示乱码。
    static func randomIdentity() -> (hostID: Data, hostName: String) {
        var bytes: [UInt8] = []
        for _ in 0..<12 { bytes.append(UInt8.random(in: 0...255)) }
        let suffix = bytes.suffix(2).map { String(format: "%02X", $0) }.joined()
        return (Data(bytes), "Z30TONE-\(suffix)")
    }

    private var fd: Int32 = -1
    private var connNumber: UInt32 = 1
    private var txn: UInt32 = 1

    private(set) var cameraName = ""
    private(set) var cameraID = ""

    // MARK: - 生命周期

    deinit { close() }

    func connect(host: String, port: UInt16 = PtpIpClient.defaultPort, timeout: TimeInterval = 8) throws {
        close()

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        guard inet_pton(AF_INET, host, &addr.sin_addr) == 1 else {
            throw PtpIpError.socket("只支持 IPv4 地址，例如 192.168.1.1")
        }

        let sock = socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { throw PtpIpError.socket("创建 socket 失败") }
        fd = sock

        var tv = timeval(tv_sec: Int(timeout), tv_usec: 0)
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        // 相机只服务一个会话，禁用 Nagle 让短包立刻发出
        var one: Int32 = 1
        setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, socklen_t(MemoryLayout<Int32>.size))

        let rc = withUnsafePointer(to: &addr) { pointer -> Int32 in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                Darwin.connect(sock, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard rc == 0 else {
            let code = errno
            close()
            throw PtpIpError.socket("连接 \(host):\(port) 失败（errno \(code)）。确认手机已加入相机 Wi-Fi")
        }
    }

    func close() {
        if fd >= 0 {
            Darwin.close(fd)
            fd = -1
        }
    }

    var isConnected: Bool { fd >= 0 }

    // MARK: - 握手

    @discardableResult
    func handshake() throws -> String {
        let request = Self.makeInitBody(hostID: hostID, hostName: hostName, conn: UInt32.random(in: 1...0xFFFF_FFFF))
        try sendPacket(request)
        let (type, ack) = try receivePacket()

        if type == 5 {
            throw PtpIpError.handshake("相机拒绝了这台主机（Init_Fail）")
        }
        guard type == 2, ack.count >= 24 else {
            throw PtpIpError.handshake("期望 Init_Ack，收到类型 \(type)")
        }

        connNumber = Self.u32(ack, 4)
        cameraID = Self.hex(ack.subdata(in: 8..<24))
        cameraName = Self.utf16String(ack, from: 24)
        return cameraName
    }

    /// 事件通道：另开一条 TCP，发 Init_Event_Request。失败不影响主通道。
    func openEventChannel() {
        // 简化处理：相机不强制要求事件通道，这里不额外占一条连接，
        // 避免在只写几个槽位时多一条链路出问题。
    }

    func openSession() throws {
        var (code, _) = try transact(opCode: 0x1002, params: [1])
        if code == 0x201E {  // SessionAlreadyOpen
            _ = try transact(opCode: 0x1003, params: [])
            (code, _) = try transact(opCode: 0x1002, params: [1])
        }
        guard code == 0x2001 else { throw PtpIpError.camera(code: code) }
    }

    // MARK: - 业务

    func deviceInfo() throws -> CameraInfo {
        let (code, data) = try transact(opCode: 0x1001, expectData: true)
        guard code == 0x2001 else { throw PtpIpError.camera(code: code) }

        var pos = 0
        pos += 2                                  // StandardVersion
        pos += 4                                  // VendorExtensionID
        pos += 2                                  // VendorExtensionVersion
        let _ = Self.ptpString(data, &pos)        // VendorExtensionDesc
        pos += 2                                  // FunctionalMode

        let ops = Self.u16Array(data, &pos)
        _ = Self.u16Array(data, &pos)             // EventsSupported
        _ = Self.u16Array(data, &pos)             // DevicePropertiesSupported
        _ = Self.u16Array(data, &pos)             // CaptureFormats
        _ = Self.u16Array(data, &pos)             // ImageFormats

        _ = Self.ptpString(data, &pos)            // Manufacturer
        let model = Self.ptpString(data, &pos)
        let firmware = Self.ptpString(data, &pos)
        let serial = Self.ptpString(data, &pos)

        return CameraInfo(
            model: model,
            firmware: firmware,
            serial: serial,
            supportsRead: ops.contains(0x90CC),
            supportsWrite: ops.contains(0x90CD)
        )
    }

    /// 读槽位原始载荷。非自定义槽位会返回 36 字节的描述符。
    func getPictCtrl(slot: Int) throws -> Data {
        let (code, data) = try transact(opCode: 0x90CC, params: [UInt32(slot), 0], expectData: true)
        guard code == 0x2001 else { throw PtpIpError.camera(code: code) }
        return data
    }

    /// 写槽位。数据放在 EndData 里——这是实测唯一被接受的分帧方式。
    func setPictCtrl(slot: Int, payload: Data) throws {
        let (code, _) = try transact(opCode: 0x90CD, params: [UInt32(slot), 0], dataOut: payload)
        guard code == 0x2001 else { throw PtpIpError.camera(code: code) }
    }

    /// 扫描出自定义槽位并映射成 C1…C9。
    ///
    /// 依据实测：Wi-Fi 下 0xC9–0xCF 返回 36 字节内置影调描述符，
    /// 614 字节的才是可写自定义槽位。按编号升序编号为 C1…C9。
    func discoverSlots(range: ClosedRange<Int> = 0xC0...0xE0) throws -> [CameraSlot] {
        var found: [CameraSlot] = []
        for slot in range {
            guard let data = try? getPictCtrl(slot: slot), data.count == 614 else { continue }
            let name = Self.payloadName(data)
            let first = Int(Self.u16be(data, 0x66))
            let last = Int(Self.u16be(data, 0x66 + 255 * 2))
            found.append(
                CameraSlot(
                    id: slot,
                    label: "C\(found.count + 1)",
                    name: name,
                    lutFirst: first,
                    lutLast: last
                )
            )
            if found.count >= 9 { break }
        }
        return found
    }

    // MARK: - 事务

    private func transact(
        opCode: UInt16,
        params: [UInt32] = [],
        dataOut: Data? = nil,
        expectData: Bool = false
    ) throws -> (UInt16, Data) {
        guard fd >= 0 else { throw PtpIpError.socket("尚未连接相机") }

        let id = txn
        txn &+= 1
        let phase: UInt32 = dataOut != nil ? 2 : (expectData ? 3 : 1)

        var body = Data()
        body.append(Self.le32(6))
        body.append(Self.le32(phase))
        body.append(Self.le16(opCode))
        body.append(Self.le32(id))
        for value in params { body.append(Self.le32(value)) }
        try sendPacket(body)

        if let payload = dataOut {
            var start = Data()
            start.append(Self.le32(9))
            start.append(Self.le32(id))
            start.append(Self.le64(UInt64(payload.count)))
            try sendPacket(start)

            var end = Data()
            end.append(Self.le32(12))
            end.append(Self.le32(id))
            end.append(payload)
            try sendPacket(end)
        }

        var collected = Data()
        while true {
            let (type, packet) = try receivePacket()
            switch type {
            case 7:
                let code = Self.u16(packet, 4)
                let packetTxn = Self.u32(packet, 6)
                if packetTxn != id { continue }
                return (code, collected)
            case 9:
                continue
            case 10, 12:
                let packetTxn = Self.u32(packet, 4)
                if packetTxn != id { continue }
                if packet.count > 8 {
                    collected.append(packet.subdata(in: 8..<packet.count))
                }
            case 8:
                continue
            default:
                continue
            }
        }
    }

    // MARK: - 收发

    private func sendPacket(_ body: Data) throws {
        var packet = Self.le32(UInt32(body.count + 4))
        packet.append(body)
        try writeAll(packet)
    }

    private func receivePacket() throws -> (UInt32, Data) {
        let head = try readExact(4)
        let total = Self.u32(head, 0)
        guard total >= 4, total < 8 * 1024 * 1024 else {
            throw PtpIpError.socket("包长度异常：\(total)")
        }
        let body = try readExact(Int(total) - 4)
        return (Self.u32(body, 0), body)
    }

    private func writeAll(_ data: Data) throws {
        guard fd >= 0 else { throw PtpIpError.socket("尚未连接相机") }
        try data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.baseAddress else { return }
            var sent = 0
            while sent < raw.count {
                let n = Darwin.send(fd, base.advanced(by: sent), raw.count - sent, 0)
                if n <= 0 { throw PtpIpError.socket("发送失败（errno \(errno)）") }
                sent += n
            }
        }
    }

    private func readExact(_ count: Int) throws -> Data {
        guard fd >= 0 else { throw PtpIpError.socket("尚未连接相机") }
        var out = Data(capacity: count)
        var buffer = [UInt8](repeating: 0, count: max(count, 1))
        while out.count < count {
            let want = count - out.count
            let n = Darwin.recv(fd, &buffer, want, 0)
            if n <= 0 {
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    throw PtpIpError.timeout
                }
                throw PtpIpError.socket("接收失败（errno \(errno)）")
            }
            out.append(contentsOf: buffer[0..<n])
        }
        return out
    }

    // MARK: - 字节工具

    static func responseText(_ code: UInt16) -> String {
        switch code {
        case 0x2001: return "OK"
        case 0x2002: return "一般错误"
        case 0x2003: return "会话未打开"
        case 0x2005: return "不支持该操作"
        case 0x2006: return "不支持该参数"
        case 0x200F: return "访问被拒"
        case 0x2019: return "设备忙"
        case 0x201E: return "会话已打开"
        case 0x201F: return "事务被取消"
        default: return "未知"
        }
    }

    private static func le16(_ value: UInt16) -> Data {
        Data([UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF)])
    }

    private static func le32(_ value: UInt32) -> Data {
        Data([
            UInt8(value & 0xFF),
            UInt8((value >> 8) & 0xFF),
            UInt8((value >> 16) & 0xFF),
            UInt8((value >> 24) & 0xFF)
        ])
    }

    private static func le64(_ value: UInt64) -> Data {
        var out = Data()
        for i in 0..<8 { out.append(UInt8((value >> (8 * UInt64(i))) & 0xFF)) }
        return out
    }

    static func u16(_ data: Data, _ offset: Int) -> UInt16 {
        guard offset + 1 < data.count else { return 0 }
        let base = data.startIndex + offset
        return UInt16(data[base]) | (UInt16(data[base + 1]) << 8)
    }

    static func u32(_ data: Data, _ offset: Int) -> UInt32 {
        guard offset + 3 < data.count else { return 0 }
        let base = data.startIndex + offset
        var value: UInt32 = 0
        for i in 0..<4 { value |= UInt32(data[base + i]) << (8 * UInt32(i)) }
        return value
    }

    static func u16be(_ data: Data, _ offset: Int) -> UInt16 {
        guard offset + 1 < data.count else { return 0 }
        let base = data.startIndex + offset
        return (UInt16(data[base]) << 8) | UInt16(data[base + 1])
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    static func utf16String(_ data: Data, from offset: Int) -> String {
        var units: [UInt16] = []
        var i = offset
        while i + 1 < data.count {
            let unit = u16(data, i)
            if unit == 0 { break }
            units.append(unit)
            i += 2
        }
        return String(decoding: units, as: UTF16.self)
    }

    private static func u16Array(_ data: Data, _ pos: inout Int) -> [UInt16] {
        let count = Int(u32(data, pos))
        pos += 4
        guard count > 0, count < 4096 else { return [] }
        var out: [UInt16] = []
        out.reserveCapacity(count)
        for _ in 0..<count {
            out.append(u16(data, pos))
            pos += 2
        }
        return out
    }

    private static func ptpString(_ data: Data, _ pos: inout Int) -> String {
        guard pos < data.count else { return "" }
        let length = Int(data[data.startIndex + pos])
        pos += 1
        guard length > 0, pos + length * 2 <= data.count else { return "" }
        var units: [UInt16] = []
        for _ in 0..<length {
            units.append(u16(data, pos))
            pos += 2
        }
        while let last = units.last, last == 0 { units.removeLast() }
        return String(decoding: units, as: UTF16.self)
    }

    static func payloadName(_ data: Data) -> String {
        guard data.count >= 22 else { return "" }
        let base = data.startIndex
        var out = ""
        for i in 2..<22 {
            let byte = data[base + i]
            if byte == 0 { break }
            if byte >= 0x20 && byte < 0x7F {
                out.append(Character(UnicodeScalar(byte)))
            }
        }
        return out.trimmingCharacters(in: .whitespaces)
    }
}
