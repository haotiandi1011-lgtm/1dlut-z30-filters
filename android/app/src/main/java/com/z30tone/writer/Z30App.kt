package com.z30tone.writer

import android.app.Application
import com.z30tone.writer.usb.UsbHub

class Z30App : Application() {
    override fun onCreate() {
        super.onCreate()
        // 提前把 USB 广播接收器装上，插拔相机都能及时响应。
        UsbHub.get(this)
    }
}
