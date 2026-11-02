plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.z30tone.writer"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.z30tone.writer"
        minSdk = 26
        targetSdk = 35
        // versionCode 只增不减（内部构建号）：0.2.0 之前已有 versionCode 3 的测试包
        // 装在真机上，回退会让测试者被迫卸载重装、丢掉本地配方与备份。
        versionCode = 3
        versionName = "0.2.0"
    }

    // 发布签名走环境变量，CI 里由 GitHub Secrets 注入；本地没有就跳过，只出 debug 包。
    val keystorePath = System.getenv("Z30_KEYSTORE")
    if (!keystorePath.isNullOrBlank() && file(keystorePath).exists()) {
        signingConfigs {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("Z30_STORE_PASSWORD")
                keyAlias = System.getenv("Z30_KEY_ALIAS")
                keyPassword = System.getenv("Z30_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")

    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")

    testImplementation("junit:junit:4.13.2")
    // Android 的 org.json 在 JVM 单测里是空壳，补一份实现让元数据解析可测。
    testImplementation("org.json:json:20240303")
}
