package com.z30tone.writer.ui

import android.app.Application
import android.hardware.usb.UsbDevice
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Recipe
import com.z30tone.writer.core.Slots
import com.z30tone.writer.core.fit.PhotoFit
import com.z30tone.writer.core.fit.PhotoLoader
import com.z30tone.writer.data.BackupStore
import com.z30tone.writer.data.BuiltinPreset
import com.z30tone.writer.data.BuiltinPresetStore
import com.z30tone.writer.data.RecipeStore
import com.z30tone.writer.usb.CameraController
import com.z30tone.writer.usb.UsbHub
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class SlotInfo(
    val slot: Int,
    val size: Int,
    val name: String? = null,
    val family: Int? = null,
    val monotonic: Boolean = true,
) {
    val label: String get() = Slots.label(slot)
    val empty: Boolean get() = size == 0
    val full: Boolean get() = size == Payload.SIZE
}

data class WriteOutcome(
    val success: Boolean,
    val title: String,
    val slotLabel: String,
    val name: String,
    val detail: String,
    val backupName: String?,
    val backupFailed: Boolean,
)

/**
 * 手机端拟合结果，只带界面要显示的东西（曲线 + 指标 + 建议）。
 * 曲线本身已经同时存成了配方包，写入走的是和 `.z30` 完全一样的那条链路。
 *
 * 指标只有「影调贴合误差（灰阶）」，单位 8 位码值 —— 单图没有真值，
 * 这里不放 ΔE、不放还原度百分比（口径见产品规划 §2）。
 */
data class FitUi(
    val name: String,
    val curve: FloatArray,
    val target: FloatArray,
    val toneDevMean: Double,
    val toneDevMax: Double,
    val blackPercent: Double,
    val whitePercent: Double,
    val maxFlatRun: Int,
    val blackGuarded: Boolean,
    val adviceLevel: String,
    val adviceText: String,
    val clipText: String,
    val meanChroma: Double,
    val width: Int,
    val height: Int,
    val millis: Long,
) {
    val ok: Boolean get() = adviceLevel != "no"
}

data class UiState(
    val cameraPresent: Boolean = false,
    val connected: Boolean = false,
    val permissionGranted: Boolean = false,
    val busy: Boolean = false,
    val busyText: String = "",
    val slots: List<SlotInfo> = emptyList(),
    val recipes: List<RecipeStore.Item> = emptyList(),
    val selectedRecipe: RecipeStore.Item? = null,
    val trashRecipes: List<RecipeStore.Item> = emptyList(),
    val builtinPresets: List<BuiltinPreset> = emptyList(),
    val hiddenPresetIds: Set<String> = emptySet(),
    val selectedSlot: Int = Slots.RECOMMENDED,
    val draftName: String = "",
    val paramsEnabled: Boolean = true,
    val sharp: Float = 0f,
    val clarity: Float = 0f,
    val saturation: Float = 0f,
    val backups: List<BackupStore.Item> = emptyList(),
    val fit: FitUi? = null,
    val log: List<String> = emptyList(),
    val message: String? = null,
    val result: WriteOutcome? = null,
)

class AppViewModel(application: Application) : AndroidViewModel(application) {

    /**
     * 「照片对比」选中的照片。
     *
     * 为什么放在 ViewModel 而不是卡片内部：卡片位于 LazyColumn 的 item 里，
     * 滚出可视区就会被回收，`remember` 的状态随之消失——用户表现为
     * 「换个预设回来就要重新选照片」（有时不走远就还在，所以时好时坏）。
     * 存在这里，卡片重新进入组合时按 URI 重新解码即可。
     */
    var comparePhotoUri by mutableStateOf<Uri?>(null)
        private set

    fun setComparePhoto(uri: Uri?) {
        comparePhotoUri = uri
    }


    private val hub = UsbHub.get(application)
    private val camera = CameraController(application)
    private val recipes = RecipeStore(application)
    private val backups = BackupStore(application)
    private val builtinStore = BuiltinPresetStore(application)

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        log("Z30 写入端已就绪")
        refreshRecipes()
        refreshBackups()
        observeUsb()
        loadBuiltins()
    }

    // -- USB 生命周期 -------------------------------------------------------

    private fun observeUsb() {
        viewModelScope.launch {
            hub.events.collect { event ->
                when (event) {
                    UsbHub.Event.ATTACHED -> {
                        _state.update { it.copy(cameraPresent = true) }
                        log("检测到相机接入")
                        prepareConnection()
                    }

                    UsbHub.Event.DETACHED -> {
                        camera.disconnect()
                        _state.update { it.copy(cameraPresent = false, connected = false, slots = emptyList()) }
                        log("相机已断开")
                    }

                    UsbHub.Event.PERMISSION_GRANTED -> {
                        log("USB 权限已授予")
                        connect()
                    }

                    UsbHub.Event.PERMISSION_DENIED -> {
                        log("USB 权限被拒绝，无法写入")
                        _state.update { it.copy(message = "USB 权限被拒绝，请在系统设置里重新授权") }
                    }
                }
            }
        }
    }

    /** Activity 拿到 USB_DEVICE_ATTACHED 的 intent 时调用（此时权限通常已自动授予）。 */
    fun onDeviceAttached(device: UsbDevice) {
        val granted = hub.manager.hasPermission(device)
        _state.update { it.copy(cameraPresent = true, permissionGranted = granted) }
        log("插上相机：${device.productName ?: "Nikon Z30"}")
        if (granted) connect() else hub.requestPermission(device)
    }

    fun onResume() {
        hub.refresh()
        val device = hub.findCamera()
        _state.update {
            it.copy(
                cameraPresent = device != null,
                permissionGranted = device != null && hub.manager.hasPermission(device),
            )
        }
        refreshBackups()
        if (device != null && !camera.connected && hub.manager.hasPermission(device) && !_state.value.busy) {
            connect()
        }
    }

    fun prepareConnection() {
        val device = hub.findCamera() ?: return
        if (hub.manager.hasPermission(device)) {
            connect()
        } else {
            log("正在请求 USB 权限…")
            hub.requestPermission(device)
        }
    }

    fun connect() {
        val device = hub.findCamera()
        if (device == null) {
            log("没找到 Z30：检查相机是否开机、USB 模式是否为 MTP/PTP")
            _state.update { it.copy(message = "没找到相机：请开机并设为 MTP/PTP 模式") }
            return
        }
        if (!hub.manager.hasPermission(device)) {
            hub.requestPermission(device)
            return
        }

        runTask("连接相机") {
            withContext(Dispatchers.IO) { camera.connect(hub.manager, device, ::log) }
            _state.update { it.copy(connected = true, permissionGranted = true) }
            log("已连接：${device.productName ?: "Nikon Z30"}")
            doReadSlots()
            log("槽位已读取")
        }
    }

    fun disconnect() {
        camera.disconnect()
        _state.update { it.copy(connected = false, slots = emptyList()) }
        log("已断开相机")
    }

    fun readSlots() {
        if (!camera.connected) return
        runTask("读取槽位") {
            doReadSlots()
            log("槽位已刷新")
        }
    }

    private suspend fun doReadSlots() {
        val list = withContext(Dispatchers.IO) {
            Slots.ALL.map { slot ->
                try {
                    val raw = camera.readSlot(slot)
                    when {
                        raw == null || raw.isEmpty() -> SlotInfo(slot, 0)
                        raw.size >= Payload.SIZE -> {
                            val parsed = Payload.parse(raw)
                            SlotInfo(slot, raw.size, parsed.name, parsed.family, parsed.monotonic)
                        }

                        else -> SlotInfo(slot, raw.size)
                    }
                } catch (e: Exception) {
                    SlotInfo(slot, 0)
                }
            }
        }
        _state.update { it.copy(slots = list) }
    }

    // -- 配方 ---------------------------------------------------------------

    fun importRecipe(uri: Uri) {
        runTask("导入配方") {
            val item = withContext(Dispatchers.IO) { recipes.import(uri) }
            refreshRecipes(prefer = item.file.absolutePath)
            log("导入配方：${item.name}")
            _state.update { it.copy(message = "已导入「${item.name}」") }
        }
    }

    fun selectRecipe(item: RecipeStore.Item) {
        if (_state.value.selectedRecipe?.file?.absolutePath == item.file.absolutePath) return
        _state.update { it.copy(selectedRecipe = item) }
        applyRecipeToDraft(item)
    }

    /** 删到回收站。想彻底删掉，去回收站里再删一次。 */
    fun deleteRecipe(item: RecipeStore.Item) {
        runCatching { recipes.delete(item) }
        log("移入回收站：${item.name}")
        _state.update { it.copy(message = "「${item.name}」已移入回收站，可随时恢复") }
        refreshRecipes()
    }

    fun deleteRecipes(items: List<RecipeStore.Item>) {
        if (items.isEmpty()) return
        items.forEach { runCatching { recipes.delete(it) } }
        log("移入回收站：${items.size} 条")
        _state.update { it.copy(message = "已把 ${items.size} 条配方移入回收站") }
        refreshRecipes()
    }

    fun restoreRecipe(item: RecipeStore.Item) {
        val ok = runCatching { recipes.restore(item) }.getOrDefault(false)
        log(if (ok) "恢复配方：${item.name}" else "恢复失败：${item.name}")
        _state.update { it.copy(message = if (ok) "已恢复「${item.name}」" else "恢复失败，文件可能已被删除") }
        refreshRecipes(prefer = if (ok) item.file.absolutePath else null)
    }

    /** 彻底删除：不可恢复，界面上会二次确认。 */
    fun purgeRecipe(item: RecipeStore.Item) {
        val ok = runCatching { recipes.purge(item) }.getOrDefault(false)
        log(if (ok) "彻底删除：${item.name}" else "彻底删除失败：${item.name}")
        _state.update { it.copy(message = if (ok) "已彻底删除「${item.name}」" else "删除失败") }
        refreshRecipes()
    }

    fun purgeTrash() {
        val n = runCatching { recipes.purgeAll() }.getOrDefault(0)
        log("清空回收站：$n 条")
        _state.update { it.copy(message = "已清空回收站（$n 条）") }
        refreshRecipes()
    }

    // -- 内置预设（随 APK 打包，免电脑）----------------------------------------

    /**
     * 读 assets 里的预设清单。曲线和参数在生成时就已经封成 614 字节载荷，
     * 手机端只是解出来给界面画图，等到用户真的选了一条才落成 `.z30`。
     */
    private fun loadBuiltins() {
        viewModelScope.launch {
            val list = withContext(Dispatchers.IO) { builtinStore.load() }
            val hidden = withContext(Dispatchers.IO) { builtinStore.hiddenIds() }
            _state.update { it.copy(builtinPresets = list, hiddenPresetIds = hidden) }
            if (list.isEmpty()) {
                log("内置预设读取失败，只能手动导入 .z30")
            } else {
                log("内置预设 ${list.size} 套已就绪（隐藏 ${hidden.size}）")
            }
        }
    }

    /** 隐藏若干内置预设。内置数据是随包发出去的，删不掉真身——隐藏后随时能恢复。 */
    fun hidePresets(ids: List<String>) {
        if (ids.isEmpty()) return
        val hidden = builtinStore.hide(ids)
        _state.update { it.copy(hiddenPresetIds = hidden) }
        log("隐藏 ${ids.size} 套内置预设")
    }

    fun restorePresets() {
        val hidden = builtinStore.unhideAll()
        _state.update { it.copy(hiddenPresetIds = hidden) }
        log("已恢复全部隐藏的预设")
    }

    /** 内置/配方库改名：只写本地别名，不动打包数据；空名字 = 恢复原名。 */
    fun renamePreset(preset: BuiltinPreset, newName: String) {
        val applied = builtinStore.setAlias(preset.id, newName)
        loadBuiltins()
        _state.update {
            it.copy(
                message = if (applied.isEmpty()) "「${preset.name}」已恢复原名"
                else "已把「${preset.name}」改名为「$applied」"
            )
        }
    }

    /** 我的配方改名：改文件里的元数据 name，载荷与文件名不变。 */
    fun renameRecipe(item: RecipeStore.Item, newName: String) {
        val name = newName.trim()
        if (name.isEmpty()) return
        runCatching { recipes.rename(item, name) }
            .onSuccess { updated ->
                refreshRecipes(updated.name)
                _state.update { it.copy(message = "已改名为「${updated.name}」") }
            }
            .onFailure { e ->
                _state.update { it.copy(message = "改名失败：${e.message ?: "写不进文件"}") }
            }
    }

    fun restorePreset(id: String) {
        val hidden = builtinStore.unhide(listOf(id))
        _state.update { it.copy(hiddenPresetIds = hidden) }
    }

    /**
     * 选一条内置预设：落成 .z30 存进本机配方库，然后选中它。
     *
     * 复用「导入配方」那条链路 —— 写入、备份、逐字节校验一行没改，
     * 内置预设和用户自己导入的配方在后续流程里完全一样。
     */
    fun installBuiltin(preset: BuiltinPreset) {
        runTask("载入「${preset.name}」") {
            val bytes = Recipe.build(preset.payload, preset.metaJson())
            val item = withContext(Dispatchers.IO) { recipes.importBytes(bytes) }
            refreshRecipes(prefer = item.file.absolutePath)
            log("载入内置预设：${preset.name}（${preset.cam}）")
            _state.update { it.copy(message = "已载入「${preset.name}」，可以写入机身了") }
        }
    }

    private fun refreshRecipes(prefer: String? = null) {
        val list = runCatching { recipes.list() }.getOrDefault(emptyList())
        val trash = runCatching { recipes.trash() }.getOrDefault(emptyList())
        val previous = _state.value.selectedRecipe?.file?.absolutePath
        val target = list.firstOrNull { it.file.absolutePath == (prefer ?: previous) } ?: list.firstOrNull()
        _state.update { it.copy(recipes = list, selectedRecipe = target, trashRecipes = trash) }
        if (target != null && target.file.absolutePath != previous) {
            applyRecipeToDraft(target)
        }
    }

    private fun applyRecipeToDraft(item: RecipeStore.Item) {
        val parsed = item.parsed
        val name = if (parsed.name.isNotEmpty()) parsed.name else item.name
        _state.update {
            it.copy(
                draftName = Payload.sanitizeName(name),
                sharp = parsed.param("sharp"),
                clarity = parsed.param("clarity"),
                saturation = parsed.param("saturation"),
                paramsEnabled = true,
            )
        }
    }

    // -- 照片拟合（手机端自己算，不依赖电脑）------------------------------------

    /**
     * 选一张照片 → 分位数匹配 → 256 点曲线 → 存成配方并自动选中。
     *
     * 拟合结果落成 `.z30` 配方包，写入走的还是原来那条链路（备份 → 写 → 逐字节校验），
     * 一行没改。手机端不做任何「自动补救」：算不出来就把原因说清楚。
     */
    fun fitPhoto(uri: Uri) {
        val app = getApplication<Application>()
        runTask("拟合照片") {
            val started = System.currentTimeMillis()
            val loaded = withContext(Dispatchers.IO) { PhotoLoader.load(app, uri) }
            log(
                "读图：%d×%d，降采样 1/%d，取 %d 个亮度点".format(
                    Locale.US, loaded.width, loaded.height, loaded.sampled, loaded.sortedLuma.size,
                ),
            )

            val result = withContext(Dispatchers.Default) {
                PhotoFit.fromLuma(loaded.sortedLuma, loaded.meanChroma)
            }
            val millis = System.currentTimeMillis() - started

            val name = PhotoFit.suggestName()
            val payload = PhotoFit.buildPayload(result, name)
            val packageBytes = Recipe.build(payload, PhotoFit.metaJson(result, name))
            val item = withContext(Dispatchers.IO) { recipes.importBytes(packageBytes) }

            val ui = FitUi(
                name = name,
                curve = result.curve01,
                target = result.target01,
                toneDevMean = result.metrics.toneDevMean,
                toneDevMax = result.metrics.toneDevMax,
                blackPercent = result.tone.black * 100.0,
                whitePercent = result.tone.white * 100.0,
                // 展示的是**原始**最长平段（可能有、但不一定构成缺陷）；anomaly 判定另走 advice
                maxFlatRun = result.banding.maxFlatRun,
                blackGuarded = result.blackGuarded,
                adviceLevel = result.advice.level,
                adviceText = result.advice.text,
                clipText = result.clipText,
                meanChroma = loaded.meanChroma,
                width = loaded.width,
                height = loaded.height,
                millis = millis,
            )
            _state.update { it.copy(fit = ui) }

            // 立刻选中新配方（槽位保持用户当前选择，默认 C9）
            refreshRecipes(prefer = item.file.absolutePath)

            log(
                "拟合完成：%d ms，判定 %s，影调贴合误差 平均 %.2f / 最大 %.2f（8 位码值）".format(
                    Locale.US,
                    millis,
                    result.advice.level,
                    result.metrics.toneDevMean,
                    result.metrics.toneDevMax,
                ),
            )
            log("曲线：黑位 %.1f%% 白位 %.1f%%%s".format(
                Locale.US,
                result.tone.black * 100,
                result.tone.white * 100,
                if (result.blackGuarded) "（黑位守卫压回过）" else "",
            ))
            _state.update {
                it.copy(
                    message = if (result.advice.ok) {
                        "已生成「$name」，选好槽位就可以写入"
                    } else {
                        result.advice.text
                    }
                )
            }
        }
    }

    // -- 写入参数 -----------------------------------------------------------

    fun setSlot(slot: Int) {
        _state.update { it.copy(selectedSlot = slot) }
    }

    fun setName(value: String) {
        _state.update { it.copy(draftName = value.take(24)) }
    }

    fun setParamsEnabled(enabled: Boolean) {
        _state.update { it.copy(paramsEnabled = enabled) }
    }

    fun setSharp(value: Float) {
        _state.update { it.copy(sharp = value) }
    }

    fun setClarity(value: Float) {
        _state.update { it.copy(clarity = value) }
    }

    fun setSaturation(value: Float) {
        _state.update { it.copy(saturation = value) }
    }

    // -- 写入与恢复 ---------------------------------------------------------

    fun write() {
        val current = _state.value
        val recipe = current.selectedRecipe ?: return
        if (!camera.connected) {
            _state.update { it.copy(message = "相机还没连上") }
            return
        }

        val slot = current.selectedSlot
        val slotInfo = current.slots.firstOrNull { it.slot == slot }
        val hadContent = slotInfo != null && !slotInfo.empty
        val name = Payload.sanitizeName(current.draftName)
        val label = Slots.label(slot)

        runTask("写入 $label") {
            var payload = Payload.withName(recipe.payload, name)
            if (current.paramsEnabled) {
                payload = Payload.setParams(
                    payload,
                    mapOf(
                        "sharp" to current.sharp,
                        "clarity" to current.clarity,
                        "saturation" to current.saturation,
                    ),
                )
            }

            try {
                val outcome = withContext(Dispatchers.IO) { camera.write(slot, payload, hadContent) }
                val success = outcome.verified
                val readbackName = runCatching { Payload.parse(outcome.readback).name }.getOrDefault("")

                _state.update {
                    it.copy(
                        result = WriteOutcome(
                            success = success,
                            title = if (success) "写入成功" else "读回校验未通过",
                            slotLabel = label,
                            name = readbackName.ifEmpty { name },
                            detail = outcome.diff.summary,
                            backupName = outcome.backup?.name,
                            backupFailed = outcome.backupFailed,
                        )
                    )
                }
                log(if (success) "写入 $label 成功，读回逐字节一致" else "写入 $label 后校验不一致：${outcome.diff.summary}")
                refreshBackups()
                runCatching { doReadSlots() }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: Exception) {
                val text = e.message?.takeIf { it.isNotBlank() } ?: "写入失败"
                log("写入失败：$text")
                _state.update { it.copy(message = text) }
                recoverSession()
            }
        }
    }

    /**
     * 写入/恢复失败后的会话恢复：废掉脏会话、重新握手。
     *
     * 失败后旧会话可能已经脏了（端点被相机 STALL、半截数据、相机休眠），不清掉的话
     * 之后的每一次传输都会失败（一加 Ace 3V 上实测「错误码 -1」就是这么来的）。
     * 这里只恢复连接，**不重发任何载荷**——重写永远由用户亲手再点一次「写入」。
     */
    private suspend fun recoverSession() {
        withContext(Dispatchers.IO) {
            camera.disconnect()
            val device = hub.findCamera()
            if (device != null && hub.manager.hasPermission(device)) {
                runCatching { camera.connect(hub.manager, device, ::log) }
                    .onFailure { t -> log("重新握手失败：${t.message}") }
            } else {
                log("相机不在线或没有 USB 权限")
            }
        }
        if (camera.connected) {
            log("已重新握手，可以直接再点一次「写入」")
            runCatching { doReadSlots() }
        } else {
            _state.update { it.copy(connected = false, slots = emptyList()) }
            log("请拔插相机后再点「连接相机」")
        }
    }

    /** 删除一份本机备份（不进回收站，删就是删；界面有二次确认）。 */
    fun deleteBackup(item: BackupStore.Item) {
        val ok = runCatching { backups.delete(item) }.getOrDefault(false)
        refreshBackups()
        if (ok) {
            log("已删除备份：${item.slotLabel} · ${item.name ?: item.file.name}")
        } else {
            _state.update { it.copy(message = "备份删除失败，再试一次") }
        }
    }

    fun restore(item: BackupStore.Item) {
        if (!item.restorable) {
            _state.update { it.copy(message = "这份备份不是 614 字节，无法原样回写") }
            return
        }
        if (!camera.connected) {
            _state.update { it.copy(message = "相机还没连上") }
            return
        }

        runTask("恢复 ${item.slotLabel}") {
            val bytes = withContext(Dispatchers.IO) { backups.read(item.file) }
            try {
                val outcome = withContext(Dispatchers.IO) { camera.write(item.slot, bytes, true) }
                val success = outcome.verified
                _state.update {
                    it.copy(
                        result = WriteOutcome(
                            success = success,
                            title = if (success) "已恢复 ${item.slotLabel}" else "恢复后校验未通过",
                            slotLabel = item.slotLabel,
                            name = item.name ?: "",
                            detail = outcome.diff.summary,
                            backupName = outcome.backup?.name,
                            backupFailed = outcome.backupFailed,
                        )
                    )
                }
                log("从备份恢复 ${item.slotLabel}：${if (success) "一致" else "不一致"}")
                refreshBackups()
                runCatching { doReadSlots() }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: Exception) {
                val text = e.message?.takeIf { it.isNotBlank() } ?: "恢复失败"
                log("恢复失败：$text")
                _state.update { it.copy(message = text) }
                recoverSession()
            }
        }
    }

    fun dismissResult() {
        _state.update { it.copy(result = null) }
    }

    fun consumeMessage() {
        _state.update { it.copy(message = null) }
    }

    // -- 内部工具 -----------------------------------------------------------

    private fun refreshBackups() {
        _state.update { it.copy(backups = runCatching { backups.list() }.getOrDefault(emptyList())) }
    }

    private fun log(text: String) {
        val stamp = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
        _state.update { it.copy(log = (it.log + "$stamp  $text").takeLast(60)) }
    }

    private fun runTask(label: String, block: suspend () -> Unit) {
        if (_state.value.busy) return
        viewModelScope.launch {
            _state.update { it.copy(busy = true, busyText = label) }
            try {
                block()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (t: Throwable) {
                val text = t.message?.takeIf { it.isNotBlank() } ?: t.javaClass.simpleName
                log("$label 失败：$text")
                _state.update { it.copy(message = text) }
            } finally {
                _state.update { it.copy(busy = false, busyText = "") }
            }
        }
    }
}
