package com.z30tone.writer.usb

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * USB 热插拔与权限的唯一入口。
 *
 * 注意：插上相机时系统会直接拉起本应用的 Activity（manifest 里的 USB_DEVICE_ATTACHED 过滤器），
 * 那种情况下 USB 权限是**自动授予**的，不需要再弹窗。
 */
class UsbHub private constructor(private val context: Context) {

    enum class Event { ATTACHED, DETACHED, PERMISSION_GRANTED, PERMISSION_DENIED }

    data class State(val cameraPresent: Boolean = false, val permissionGranted: Boolean = false)

    val manager: UsbManager = context.getSystemService(Context.USB_SERVICE) as UsbManager

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    private val _events = MutableSharedFlow<Event>(extraBufferCapacity = 8)
    val events: SharedFlow<Event> = _events.asSharedFlow()

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(receiverContext: Context?, intent: Intent?) {
            when (intent?.action) {
                ACTION_PERMISSION -> {
                    val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
                    refresh()
                    _events.tryEmit(if (granted) Event.PERMISSION_GRANTED else Event.PERMISSION_DENIED)
                }

                UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                    refresh()
                    _events.tryEmit(Event.ATTACHED)
                }

                UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                    refresh()
                    _events.tryEmit(Event.DETACHED)
                }
            }
        }
    }

    init {
        val filter = IntentFilter().apply {
            addAction(ACTION_PERMISSION)
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        // 注册失败也不致命：Activity 的 manifest 过滤器仍能拉起应用，onResume 会再扫一次。
        runCatching {
            ContextCompat.registerReceiver(context, receiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        }
        refresh()
    }

    fun findCamera(): UsbDevice? =
        manager.deviceList.values.firstOrNull { UsbPtp.isCamera(it) }

    fun refresh() {
        val device = findCamera()
        _state.value = State(
            cameraPresent = device != null,
            permissionGranted = device != null && manager.hasPermission(device),
        )
    }

    fun requestPermission(device: UsbDevice) {
        val intent = Intent(ACTION_PERMISSION).setPackage(context.packageName)
        // 系统要往这个 PendingIntent 里回填 EXTRA_DEVICE / EXTRA_PERMISSION_GRANTED，
        // Android 12+ 必须用 FLAG_MUTABLE。
        val mutable = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            PendingIntent.FLAG_MUTABLE
        } else {
            0
        }
        val pending = PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or mutable,
        )
        manager.requestPermission(device, pending)
    }

    companion object {
        private const val ACTION_PERMISSION = "com.z30tone.writer.USB_PERMISSION"

        // 这里只保存 applicationContext（见 get()），生命周期与进程一致，不存在泄漏。
        @SuppressLint("StaticFieldLeak")
        @Volatile
        private var instance: UsbHub? = null

        fun get(context: Context): UsbHub =
            instance ?: synchronized(this) {
                instance ?: UsbHub(context.applicationContext).also { instance = it }
            }
    }
}
