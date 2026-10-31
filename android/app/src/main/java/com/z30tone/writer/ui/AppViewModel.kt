package com.z30tone.writer.ui

import android.app.Application
import android.hardware.usb.UsbDevice
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Slots
import com.z30tone.writer.data.BackupStore
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

data class UiState(
    val cameraPresent: Boolean = false,
    val connected: Boolean = false,
    val permissionGranted: Boolean = false,
    val busy: Boolean = false,
    val busyText: String = "",
    val slots: List<SlotInfo> = emptyList(),
    val recipes: List<RecipeStore.Item> = emptyList(),
    val selectedRecipe: RecipeStore.Item? = null,
    val selectedSlot: Int = Slots.RECOMMENDED,
    val draftName: String = "",
    val paramsEnabled: Boolean = true,
    val sharp: Float = 0f,
    val clarity: Float = 0f,
    val saturation: Float = 0f,
    val backups: List<BackupStore.Item> = emptyList(),
    val log: List<String> = emptyList(),
    val message: String? = null,
    val result: WriteOutcome? = null,
)

class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val hub = UsbHub.get(application)
    private val camera = CameraController(application)
    private val recipes = RecipeStore(application)
    private val backups = BackupStore(application)

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        log("Z30 写入端已就绪")
        refreshRecipes()
        refreshBackups()
        observeUsb()
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

    fun deleteRecipe(item: RecipeStore.Item) {
        runCatching { recipes.delete(item) }
        log("删除配方：${item.name}")
        refreshRecipes()
    }

    private fun refreshRecipes(prefer: String? = null) {
        val list = runCatching { recipes.list() }.getOrDefault(emptyList())
        val previous = _state.value.selectedRecipe?.file?.absolutePath
        val target = list.firstOrNull { it.file.absolutePath == (prefer ?: previous) } ?: list.firstOrNull()
        _state.update { it.copy(recipes = list, selectedRecipe = target) }
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
