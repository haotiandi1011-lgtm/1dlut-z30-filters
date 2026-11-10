package com.z30tone.writer.ui

import android.graphics.Bitmap
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Preview
import com.z30tone.writer.core.Slots
import com.z30tone.writer.data.BackupStore
import com.z30tone.writer.data.BuiltinPreset
import com.z30tone.writer.data.RecipeStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WriterScreen(viewModel: AppViewModel) {
    val state by viewModel.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    var confirmWrite by remember { mutableStateOf(false) }
    var showBackups by remember { mutableStateOf(false) }
    var showLibrary by remember { mutableStateOf(false) }
    var showTrash by remember { mutableStateOf(false) }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) viewModel.importRecipe(uri)
    }

    val photoPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) viewModel.fitPhoto(uri)
    }

    LaunchedEffect(state.message) {
        val message = state.message
        if (message != null) {
            snackbar.showSnackbar(message)
            viewModel.consumeMessage()
        }
    }

    state.result?.let { outcome ->
        ResultDialog(outcome = outcome, onDismiss = viewModel::dismissResult)
    }

    if (confirmWrite) {
        ConfirmDialog(
            state = state,
            onDismiss = { confirmWrite = false },
            onConfirm = {
                confirmWrite = false
                viewModel.write()
            },
        )
    }

    if (showBackups) {
                    BackupsDialog(
                        items = state.backups,
                        onDismiss = { showBackups = false },
                        onRestore = { item ->
                            showBackups = false
                            viewModel.restore(item)
                        },
                        onDelete = { item ->
                            viewModel.deleteBackup(item)
                        },
                    )
    }

    if (showLibrary) {
        PresetLibraryDialog(
            presets = state.builtinPresets,
            hiddenIds = state.hiddenPresetIds,
            onDismiss = { showLibrary = false },
            onPick = { preset ->
                showLibrary = false
                viewModel.installBuiltin(preset)
            },
            onHide = { ids -> viewModel.hidePresets(ids) },
            onRestoreAll = { viewModel.restorePresets() },
            onRestoreOne = { id -> viewModel.restorePreset(id) },
            onRename = { preset, name -> viewModel.renamePreset(preset, name) },
        )
    }

    if (showTrash) {
        TrashDialog(
            items = state.trashRecipes,
            onDismiss = { showTrash = false },
            onRestore = viewModel::restoreRecipe,
            onPurge = viewModel::purgeRecipe,
            onPurgeAll = viewModel::purgeTrash,
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Z30 影调", style = MaterialTheme.typography.titleLarge)
                        Text(
                            text = "照片拟合 → 曲线 → 写入机身",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        bottomBar = {
            WriteBar(state = state, onWrite = { confirmWrite = true })
        },
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            item {
                DeviceCard(
                    state = state,
                    onConnect = viewModel::connect,
                    onDisconnect = viewModel::disconnect,
                    onRefresh = viewModel::readSlots,
                    onBackups = { showBackups = true },
                )
            }
            item {
                PhotoFitCard(
                    state = state,
                    onPickPhoto = {
                        photoPicker.launch(arrayOf("image/jpeg", "image/png", "image/heic", "image/heif"))
                    },
                )
            }
            item {
                SlotCard(state = state, onSelect = viewModel::setSlot)
            }
            item {
                BuiltinLibraryCard(
                    state = state,
                    onOpen = { showLibrary = true },
                )
            }
            item {
                val chosen = state.selectedRecipe
                PhotoCompareCard(
                    lut = chosen?.parsed?.lut,
                    presetName = chosen?.name ?: "未选预设",
                    photoUri = viewModel.comparePhotoUri,
                    onPickPhoto = viewModel::setComparePhoto,
                )
            }
            item {
                RecipeCard(
                    state = state,
                    onRename = viewModel::renameRecipe,
                    onImport = { picker.launch(arrayOf("*/*")) },
                    onSelect = viewModel::selectRecipe,
                    onDelete = viewModel::deleteRecipe,
                    onDeleteMany = viewModel::deleteRecipes,
                    onOpenTrash = { showTrash = true },
                )
            }
            item {
                WriteCard(state = state, viewModel = viewModel)
            }
            item {
                LogCard(log = state.log)
            }
        }
    }
}

// -- 卡片骨架 ---------------------------------------------------------------

@Composable
private fun Section(
    title: String,
    subtitle: String? = null,
    trailing: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(title, style = MaterialTheme.typography.titleMedium)
                    if (subtitle != null) {
                        Text(
                            text = subtitle,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                trailing?.invoke()
            }
            content()
        }
    }
}

// -- 相机 -------------------------------------------------------------------

@Composable
private fun DeviceCard(
    state: UiState,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onRefresh: () -> Unit,
    onBackups: () -> Unit,
) {
    Section(
        title = "相机",
        subtitle = if (state.connected) "USB 已连通，可以读写槽位" else "USB 连接相机后会自动识别",
        trailing = {
            TextButton(onClick = onBackups, enabled = !state.busy) {
                Text("备份 ${state.backups.size}")
            }
        },
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(9.dp)
                    .background(statusColor(state), CircleShape),
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(statusTitle(state), style = MaterialTheme.typography.bodyLarge)
                Text(
                    text = statusHint(state),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (state.connected) {
                OutlinedButton(onClick = onDisconnect, enabled = !state.busy) { Text("断开") }
                OutlinedButton(onClick = onRefresh, enabled = !state.busy) { Text("刷新槽位") }
            } else {
                Button(onClick = onConnect, enabled = !state.busy) {
                    Text(if (state.cameraPresent) "重试连接" else "连接相机")
                }
            }
        }
    }
}

@Composable
private fun statusColor(state: UiState): Color = when {
    state.connected -> MaterialTheme.colorScheme.primary
    state.cameraPresent -> MaterialTheme.colorScheme.secondary
    else -> MaterialTheme.colorScheme.outline
}

private fun statusTitle(state: UiState): String = when {
    state.connected -> "Nikon Z30 已连接"
    state.cameraPresent && state.permissionGranted -> "已检测到相机，但没连上"
    state.cameraPresent -> "已检测到相机，等待 USB 授权"
    else -> "未连接"
}

private fun statusHint(state: UiState): String = when {
    state.busy -> state.busyText.ifEmpty { "处理中…" }
    state.connected -> "读 C1–C9、写目标槽位、写后逐字节校验"
    state.cameraPresent && state.permissionGranted -> "点「重试连接」；卡在哪一步看下方运行日志"
    state.cameraPresent -> "系统弹窗里点「允许」即可"
    else -> "相机开机 → USB 模式设为 MTP/PTP → 插上数据线"
}

// -- 照片拟合 ---------------------------------------------------------------

@Composable
private fun PhotoFitCard(state: UiState, onPickPhoto: () -> Unit) {
    Section(
        title = "从照片生成曲线",
        subtitle = "不用电脑：选一张照片，手机上直接算 256 点曲线",
        trailing = {
            TextButton(onClick = onPickPhoto, enabled = !state.busy) {
                Text(if (state.fit == null) "选照片" else "换一张")
            }
        },
    ) {
        val fit = state.fit
        if (fit == null) {
            Text(
                text = "选一张你喜欢的照片，把它的影调分布迁到「典型场景」上，得到一条单调曲线。" +
                    "支持 JPG / PNG / HEIC；RAW 手机解不了（系统没有相机解码器），NEF 之类用电脑端拟合。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            CurveCompareChart(
                curve = fit.curve,
                target = fit.target,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(140.dp),
            )
            Text(
                text = "粗线 = 拟合曲线　细线 = 分位数匹配的目标　斜线 = 恒等",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                Metric("贴合误差 平均", "%.1f".format(Locale.US, fit.toneDevMean))
                Metric("贴合误差 最大", "%.1f".format(Locale.US, fit.toneDevMax))
                Metric("曲线", if (fit.maxFlatRun > 1) "平段 ${fit.maxFlatRun}" else "单调")
            }
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                Metric("黑位", "%.1f%%".format(Locale.US, fit.blackPercent))
                Metric("白位", "%.1f%%".format(Locale.US, fit.whitePercent))
                Metric("彩度", "%.0f".format(Locale.US, fit.meanChroma))
            }

            Text(
                text = "贴合误差 = 曲线与目标的逐点差，单位是 8 位码值（0–255）；它不是 ΔE。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            AdviceBox(fit)

            Text(
                text = "${fit.width}×${fit.height} · 用时 ${fit.millis} ms · ${fit.clipText}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "单图模式只评估灰阶影调：曲线对三通道是同一个映射，参考图的色彩个性" +
                    "（色偏、饱和度、分色相）量化不了，所以这里不给 ΔE、也不给还原度百分比。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "已存为配方「${fit.name}」，下面选槽位就能写入（默认 C9）。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun AdviceBox(fit: FitUi) {
    val container = when (fit.adviceLevel) {
        "no" -> MaterialTheme.colorScheme.errorContainer
        "caution" -> MaterialTheme.colorScheme.secondaryContainer
        else -> MaterialTheme.colorScheme.primaryContainer
    }
    val content = when (fit.adviceLevel) {
        "no" -> MaterialTheme.colorScheme.onErrorContainer
        "caution" -> MaterialTheme.colorScheme.onSecondaryContainer
        else -> MaterialTheme.colorScheme.onPrimaryContainer
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(container)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        Text(
            text = when (fit.adviceLevel) {
                "no" -> "不能写入"
                "caution" -> "能写，但先看一眼"
                else -> "可以写"
            },
            style = MaterialTheme.typography.labelLarge,
            color = content,
        )
        Text(fit.adviceText, style = MaterialTheme.typography.bodySmall, color = content)
        if (fit.blackGuarded) {
            Text(
                text = "黑位守卫介入了：拟合出的黑位比目标高，已压回目标值。",
                style = MaterialTheme.typography.labelSmall,
                color = content,
            )
        }
    }
}

@Composable
private fun CurveCompareChart(curve: FloatArray, target: FloatArray?, modifier: Modifier) {
    val gridColor = MaterialTheme.colorScheme.outlineVariant
    val curveColor = MaterialTheme.colorScheme.primary
    val targetColor = MaterialTheme.colorScheme.secondary

    Canvas(modifier = modifier) {
        val width = size.width
        val height = size.height
        if (width <= 0f || height <= 0f) return@Canvas

        for (i in 0..4) {
            val x = width * i / 4f
            val y = height * i / 4f
            drawLine(gridColor, Offset(x, 0f), Offset(x, height), strokeWidth = 1f)
            drawLine(gridColor, Offset(0f, y), Offset(width, y), strokeWidth = 1f)
        }
        // 恒等参考线：曲线在它上方 = 提亮该段
        drawLine(gridColor, Offset(0f, height), Offset(width, 0f), strokeWidth = 1.5f)

        fun pathOf(values: FloatArray): Path {
            val path = Path()
            val last = (values.size - 1).coerceAtLeast(1)
            values.forEachIndexed { index, value ->
                val x = width * index / last.toFloat()
                val y = height * (1f - value.coerceIn(0f, 1f))
                if (index == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            return path
        }

        if (target != null) {
            drawPath(pathOf(target), color = targetColor, style = Stroke(width = 1.5.dp.toPx()))
        }
        drawPath(
            path = pathOf(curve),
            color = curveColor,
            style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round),
        )
    }
}

// -- 槽位 -------------------------------------------------------------------

@Composable
private fun SlotCard(state: UiState, onSelect: (Int) -> Unit) {
    Section(title = "目标槽位", subtitle = "首次使用建议写 C9，写前会自动备份") {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            for (row in 0 until 3) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (column in 0 until 3) {
                        val slot = Slots.FIRST + row * 3 + column
                        SlotCell(
                            slot = slot,
                            info = state.slots.firstOrNull { it.slot == slot },
                            selected = state.selectedSlot == slot,
                            enabled = !state.busy,
                            onClick = { onSelect(slot) },
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        }
        Text(
            text = if (state.slots.isEmpty()) {
                "连上相机后这里会显示每个槽位的名称与形态。"
            } else {
                "「仅参数」是机身自己建的槽位，里面没有曲线，写入后会被完整配方替换。"
            },
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun SlotCell(
    slot: Int,
    info: SlotInfo?,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier,
) {
    val container = if (selected) {
        MaterialTheme.colorScheme.primaryContainer
    } else {
        MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)
    }
    val stroke = if (selected) {
        MaterialTheme.colorScheme.primary
    } else {
        MaterialTheme.colorScheme.outlineVariant
    }
    val textColor = if (selected) {
        MaterialTheme.colorScheme.onPrimaryContainer
    } else {
        MaterialTheme.colorScheme.onSurfaceVariant
    }

    Column(
        modifier = modifier
            .clip(RoundedCornerShape(14.dp))
            .background(container)
            .border(if (selected) 1.5.dp else 1.dp, stroke, RoundedCornerShape(14.dp))
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 9.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = Slots.label(slot),
                style = MaterialTheme.typography.labelLarge,
                color = textColor,
            )
            Spacer(modifier = Modifier.weight(1f))
            if (slot == Slots.RECOMMENDED) {
                Text(
                    text = "推荐",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = slotStatusText(info),
            // 允许两行：窄屏（三列格子）下「36 字节 · 仅参数」一行放不下，
            // 之前省略号把最关键的「仅参数」吞掉了。字号也降到 labelSmall 留出余量。
            style = MaterialTheme.typography.labelSmall,
            color = textColor,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            lineHeight = 14.sp,
        )
    }
}

private fun slotStatusText(info: SlotInfo?): String = when {
    info == null -> "未读取"
    info.empty -> "空"
    info.full -> info.name?.ifBlank { "无名称" } ?: "614 字节"
    else -> "${info.size} 字节 · 仅参数"
}

// -- 内置预设 ---------------------------------------------------------------

/** 另存为对话框的默认文件名：曲线名（转 ASCII）+ 时间 + 扩展名，像 `z30tone-FL03-MUTE-1102-1418.png`。 */
private fun suggestedExportName(presetName: String, ext: String): String {
    val clean = presetName.filter { it.code in 0x21..0x7E && it !in "\\/:*?\"<>|" }
        .replace(" ", "-")
        .take(24)
        .ifEmpty { "photo" }
    val stamp = java.text.SimpleDateFormat("MMdd-HHmm", java.util.Locale.US)
        .format(java.util.Date())
    return "z30tone-$clean-$stamp.$ext"
}

@Composable
private fun PhotoCompareCard(
    lut: IntArray?,
    presetName: String,
    photoUri: Uri?,
    onPickPhoto: (Uri?) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var picked by remember { mutableStateOf<Preview.Loaded?>(null) }
    var after by remember { mutableStateOf<Bitmap?>(null) }
    var note by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var split by remember { mutableFloatStateOf(0.5f) }
    var exporting by remember { mutableStateOf(false) }

    // 选择回调只负责「记下这张照片」，解码统一放在下面的 LaunchedEffect：
    // 卡片滚出屏幕被回收、再回来时，也能按 URI 重新解码出画面对比。
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        // 尽量拿持久读权限：卡片重建、甚至进程回收后重新解码都不至于读不到
        runCatching {
            context.contentResolver.takePersistableUriPermission(
                uri, android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        }
        onPickPhoto(uri)
    }

    LaunchedEffect(photoUri) {
        if (photoUri == null) {
            picked = null
            return@LaunchedEffect
        }
        error = null
        try {
            val loaded = withContext(Dispatchers.IO) { Preview.load(context, photoUri) }
            picked = loaded
            note = loaded.note
        } catch (e: Exception) {
            error = e.message ?: "这张照片读不了"
            picked = null
        }
    }

    // 「保存」先选格式（PNG 无损 / JPG 高质量），再走系统另存为挑目录和文件名；
    // App 不碰另存为位置以外的任何存储。
    var showFormatPicker by remember { mutableStateOf(false) }

    val saveAs: (Uri?, Bitmap.CompressFormat, Int, String) -> Unit = { uri, format, quality, label ->
        val src = photoUri
        val table = lut
        if (uri != null && src != null && table != null) {
            error = null
            exporting = true
            scope.launch {
                try {
                    val result = withContext(Dispatchers.IO) {
                        context.contentResolver.openOutputStream(uri)?.use { out ->
                            Preview.exportImage(context, src, Preview.buildLut(table), out, format, quality)
                        } ?: throw IllegalArgumentException("保存位置打不开")
                    }
                    note = "已保存 ${result.width}×${result.height} $label" +
                        if (result.shrunk) "（原图超上限，按比例缩小导出）" else ""
                    Toast.makeText(context, "已保存（$label）", Toast.LENGTH_SHORT).show()
                } catch (e: Exception) {
                    error = e.message ?: "保存失败"
                    Toast.makeText(context, error ?: "保存失败", Toast.LENGTH_SHORT).show()
                } finally {
                    exporting = false
                }
            }
        }
    }
    val savePng = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("image/png")) { uri: Uri? ->
        saveAs(uri, Bitmap.CompressFormat.PNG, 100, "PNG 无损")
    }
    val saveJpg = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("image/jpeg")) { uri: Uri? ->
        saveAs(uri, Bitmap.CompressFormat.JPEG, 95, "JPG 高质量")
    }

    // 曲线换了（切了别的预设）就重算「效果」那半边
    LaunchedEffect(picked, lut) {
        val src = picked?.bitmap
        after = when {
            src == null || lut == null -> null
            else -> withContext(Dispatchers.Default) { Preview.applyLut(src, Preview.buildLut(lut)) }
        }
    }

    Section(
        title = "照片对比",
        subtitle = "用你自己的照片看这条曲线：左边原图、右边套用后，中间线能拖",
        trailing = {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(
                    onClick = { showFormatPicker = true },
                    enabled = after != null && photoUri != null && !exporting,
                ) {
                    Text(if (exporting) "导出中…" else "保存")
                }
                TextButton(onClick = { picker.launch(arrayOf("image/*")) }) {
                    Text(if (picked == null) "选照片" else "换一张")
                }
            }
        },
    ) {
        when {
            lut == null -> Text(
                text = "先在上面选一条预设或配方，这里才有曲线可套。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            picked == null -> Text(
                text = "点右上「选照片」，选一张你拍的照片（JPG / PNG / HEIC）。" +
                    "选好后会自动套上「$presetName」这条曲线，拖动分割线就能对比。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            else -> {
                val src = picked!!.bitmap
                val ratio = src.width.toFloat() / src.height.toFloat()
                val shown = after ?: src
                BoxWithConstraints(
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(ratio)
                        .clip(RoundedCornerShape(12.dp))
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                        .pointerInput(Unit) {
                            detectHorizontalDragGestures { change, _ ->
                                split = (change.position.x / size.width).coerceIn(0f, 1f)
                            }
                        },
                ) {
                    Image(
                        bitmap = shown.asImageBitmap(),
                        contentDescription = "套用后",
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize(),
                    )
                    Image(
                        bitmap = src.asImageBitmap(),
                        contentDescription = "原图",
                        contentScale = ContentScale.Fit,
                        modifier = Modifier
                            .fillMaxSize()
                            .drawWithContent {
                                clipRect(right = size.width * split) { this@drawWithContent.drawContent() }
                            },
                    )
                    val x = maxWidth * split
                    Box(
                        modifier = Modifier
                            .offset(x = x - 1.dp)
                            .width(2.dp)
                            .fillMaxHeight()
                            .background(Color.White.copy(alpha = 0.9f)),
                    )
                    Box(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .offset(x = x - 14.dp)
                            .size(28.dp)
                            .background(Color.White.copy(alpha = 0.92f), CircleShape),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text("‹›", style = MaterialTheme.typography.labelSmall, color = Color.Black)
                    }
                }
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    text = "$note · 拖动画面上的分割线 · 当前曲线：$presetName",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        error?.let {
            Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
        }
    }

    if (showFormatPicker) {
        AlertDialog(
            onDismissRequest = { showFormatPicker = false },
            title = { Text("保存为什么格式？") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        "两个都是这张照片套完曲线后的样子，区别只在文件本身：",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text("PNG · 无损 —— 所见即所得，适合留档，文件大", style = MaterialTheme.typography.bodySmall)
                    Text("JPG · 高质量 95 —— 体积小，适合分享，有轻微压缩", style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    showFormatPicker = false
                    savePng.launch(suggestedExportName(presetName, "png"))
                }) { Text("PNG 无损") }
            },
            dismissButton = {
                TextButton(onClick = {
                    showFormatPicker = false
                    saveJpg.launch(suggestedExportName(presetName, "jpg"))
                }) { Text("JPG 高质量") }
            },
        )
    }
}

// -- 内置预设库 -------------------------------------------------------------

@Composable
private fun BuiltinLibraryCard(state: UiState, onOpen: () -> Unit) {
    val count = state.builtinPresets.count { it.id !in state.hiddenPresetIds }
    val hidden = state.hiddenPresetIds.size
    Section(
        title = "内置预设",
        subtitle = if (state.builtinPresets.isEmpty()) "正在读取…"
        else "$count 套随 App 打包，不用电脑" + if (hidden > 0) "（隐藏 $hidden）" else "",
        trailing = {
            TextButton(onClick = onOpen, enabled = state.builtinPresets.isNotEmpty() && !state.busy) { Text("挑选") }
        },
    ) {
        Text(
            text = "曲线、参数、机身名都已经封装在 App 里。选中哪条就把它存进本机配方库，"
                + "之后和导入的配方走同一条写入流程。"
                + "不想要的用「管理→隐藏」，隐藏后挑选列表就不再出现（内置数据只读，没有真删除）；"
                + "已载入配方库的拷贝是独立的，删它不影响这里。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        state.selectedRecipe?.let { item ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                CurveChart(lut = item.parsed.lut, modifier = Modifier.size(42.dp, 32.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "当前：${item.name}",
                        style = MaterialTheme.typography.bodyMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = item.source ?: "内置预设",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
    }
}

@Composable
private fun PresetLibraryDialog(
    presets: List<BuiltinPreset>,
    hiddenIds: Set<String>,
    onDismiss: () -> Unit,
    onPick: (BuiltinPreset) -> Unit,
    onHide: (List<String>) -> Unit,
    onRestoreAll: () -> Unit,
    onRestoreOne: (String) -> Unit,
    onRename: (BuiltinPreset, String) -> Unit,
) {
    var renaming by remember { mutableStateOf<BuiltinPreset?>(null) }
    var query by remember { mutableStateOf("") }
    var managing by remember { mutableStateOf(false) }
    var showHidden by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<Set<String>>(emptySet()) }
    var collapsed by remember { mutableStateOf<Set<String>>(emptySet()) }

    val match = { p: BuiltinPreset ->
        val k = query.trim()
        k.isEmpty() || p.name.contains(k, true) || p.cat.contains(k, true) ||
            p.cam.contains(k, true) || p.desc.contains(k, true)
    }
    val visible = remember(query, presets, hiddenIds) {
        presets.filter { it.id !in hiddenIds && match(it) }
    }
    val hiddenList = remember(query, presets, hiddenIds) {
        presets.filter { it.id in hiddenIds && match(it) }
    }
    val grouped = remember(visible) {
        val map = LinkedHashMap<String, MutableList<BuiltinPreset>>()
        visible.forEach { map.getOrPut(it.cat) { mutableListOf() }.add(it) }
        map
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                if (hiddenIds.isEmpty()) "内置预设 · ${visible.size}/${presets.size}"
                else "内置预设 · ${visible.size}/${presets.size}（隐藏 ${hiddenIds.size}）",
            )
        },
        text = {
            Column {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    singleLine = true,
                    label = { Text("搜索") },
                    placeholder = { Text("名字 / 分类 / 机身名") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = {
                        managing = !managing
                        if (!managing) selected = emptySet()
                    }) { Text(if (managing) "完成" else "管理") }
                    if (hiddenIds.isNotEmpty()) {
                        TextButton(onClick = { showHidden = !showHidden }) {
                            Text(if (showHidden) "收起已隐藏" else "已隐藏 ${hiddenIds.size}")
                        }
                    }
                    Spacer(modifier = Modifier.weight(1f))
                    if (managing && selected.isNotEmpty()) {
                        TextButton(onClick = {
                            onHide(selected.toList())
                            selected = emptySet()
                            managing = false
                        }) { Text("隐藏所选 ${selected.size}") }
                    }
                }
                if (managing) {
                    Text(
                        text = "勾选后点右上「隐藏所选」：预设从挑选列表消失，随时可恢复；" +
                            "已载入配方库的拷贝不受影响。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                if (showHidden && hiddenList.isNotEmpty()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = "已隐藏 · 点「恢复」就回到列表",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = onRestoreAll) { Text("全部恢复") }
                    }
                    LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 160.dp)) {
                        items(hiddenList, key = { "h:" + it.id }) { preset ->
                            HiddenRow(preset = preset, onRestore = { onRestoreOne(preset.id) })
                        }
                    }
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                }

                if (visible.isEmpty()) {
                    Text(
                        text = "没有匹配的预设。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 340.dp)) {
                        grouped.forEach { (cat, catItems) ->
                            item(key = "cat:$cat") {
                                // 板块标题可点：展开 / 收起。121 套里用户往往只看一两个板块，
                                // 收起不看的不用一直滚。
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable {
                                            collapsed = if (cat in collapsed) collapsed - cat
                                            else collapsed + cat
                                        }
                                        .padding(top = 10.dp, bottom = 4.dp),
                                ) {
                                    Text(
                                        text = if (cat in collapsed) "▸" else "▾",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Spacer(modifier = Modifier.width(4.dp))
                                    Text(
                                        text = "$cat · ${catItems.size}",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            if (cat !in collapsed) {
                                items(catItems, key = { it.id }) { preset ->
                                    BuiltinRow(
                                        preset = preset,
                                        managing = managing,
                                        checked = preset.id in selected,
                                        onPick = onPick,
                                        onRename = { renaming = preset },
                                        onToggle = {
                                            selected = if (preset.id in selected) selected - preset.id
                                            else selected + preset.id
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("关闭") }
        },
    )

    renaming?.let { preset ->
        RenameDialog(
            title = "给「${preset.name}」改名",
            initial = preset.name,
            canReset = true,
            onDismiss = { renaming = null },
            onConfirm = { newName ->
                renaming = null
                onRename(preset, newName)
            },
        )
    }
}

@Composable
private fun BuiltinRow(
    preset: BuiltinPreset,
    managing: Boolean,
    checked: Boolean,
    onPick: (BuiltinPreset) -> Unit,
    onToggle: () -> Unit,
    onRename: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .clickable { if (managing) onToggle() else onPick(preset) }
            .padding(horizontal = 8.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (managing) {
            Box(
                modifier = Modifier
                    .size(18.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(
                        if (checked) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surface,
                    )
                    .border(
                        1.5.dp,
                        if (checked) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.outline,
                        RoundedCornerShape(4.dp),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (checked) Text("✓", style = MaterialTheme.typography.labelSmall, color = Color.White)
            }
        } else {
            CurveChart(lut = preset.lut, modifier = Modifier.size(42.dp, 32.dp))
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = preset.name,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = preset.subtitle,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (!managing) {
            TextButton(onClick = onRename, contentPadding = PaddingValues(horizontal = 6.dp)) {
                Text("改名", style = MaterialTheme.typography.labelSmall)
            }
            Text(
                text = "载入",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun HiddenRow(preset: BuiltinPreset, onRestore: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = preset.name,
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onRestore) { Text("恢复") }
    }
}

// -- 配方 -------------------------------------------------------------------

@Composable
private fun RecipeCard(
    state: UiState,
    onImport: () -> Unit,
    onRename: (RecipeStore.Item, String) -> Unit,
    onSelect: (RecipeStore.Item) -> Unit,
    onDelete: (RecipeStore.Item) -> Unit,
    onDeleteMany: (List<RecipeStore.Item>) -> Unit,
    onOpenTrash: () -> Unit,
) {
    var managing by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<Set<String>>(emptySet()) }
    var renaming by remember { mutableStateOf<RecipeStore.Item?>(null) }
    val trashCount = state.trashRecipes.size

    Section(
        title = "配方",
        subtitle = "删掉的进回收站，能恢复也能彻底删除。名字下标着「内置预设」的是挑选时存下的拷贝：" +
            "删它不影响内置预设列表；想从挑选列表去掉某套预设，去 内置预设→管理→隐藏",
        trailing = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (state.recipes.isNotEmpty()) {
                    TextButton(onClick = {
                        managing = !managing
                        if (!managing) selected = emptySet()
                    }) { Text(if (managing) "完成" else "管理") }
                }
                if (trashCount > 0) {
                    TextButton(onClick = onOpenTrash) { Text("回收站 $trashCount") }
                }
                TextButton(onClick = onImport, enabled = !state.busy) { Text("导入") }
            }
        },
    ) {
        if (managing && selected.isNotEmpty()) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "已选 ${selected.size} 条",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(modifier = Modifier.weight(1f))
                TextButton(onClick = {
                    onDeleteMany(state.recipes.filter { it.file.absolutePath in selected })
                    selected = emptySet()
                    managing = false
                }) { Text("移入回收站") }
            }
        }

        if (state.recipes.isEmpty()) {
            Text(
                text = if (trashCount > 0) "配方列表是空的。回收站里还有 $trashCount 条，点右上角可以恢复。"
                else "还没有配方。点右上角「导入」，选一个 .z30 文件。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                state.recipes.forEach { item ->
                    RecipeRow(
                        item = item,
                        selected = state.selectedRecipe?.file?.absolutePath == item.file.absolutePath,
                        managing = managing,
                        checked = item.file.absolutePath in selected,
                        onClick = {
                            if (managing) {
                                selected = if (item.file.absolutePath in selected) {
                                    selected - item.file.absolutePath
                                } else {
                                    selected + item.file.absolutePath
                                }
                            } else {
                                onSelect(item)
                            }
                        },
                        onDelete = { onDelete(item) },
                        onRename = { renaming = item },
                    )
                }
            }
        }
    }

    renaming?.let { item ->
        RenameDialog(
            title = "给配方改名",
            initial = item.name,
            canReset = false,
            onDismiss = { renaming = null },
            onConfirm = { newName ->
                renaming = null
                onRename(item, newName)
            },
        )
    }
}

@Composable
private fun RecipeRow(
    item: RecipeStore.Item,
    selected: Boolean,
    managing: Boolean,
    checked: Boolean,
    onClick: () -> Unit,
    onDelete: () -> Unit,
    onRename: () -> Unit,
) {
    val background = if (selected && !managing) {
        MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.6f)
    } else {
        Color.Transparent
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(background)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (managing) {
            Box(
                modifier = Modifier
                    .size(18.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(
                        if (checked) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surface,
                    )
                    .border(
                        1.5.dp,
                        if (checked) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.outline,
                        RoundedCornerShape(4.dp),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (checked) Text("✓", style = MaterialTheme.typography.labelSmall, color = Color.White)
            }
        } else {
            CurveChart(lut = item.parsed.lut, modifier = Modifier.size(42.dp, 32.dp))
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = item.name,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = recipeMeta(item),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (!item.parsed.monotonic) {
            Text(
                text = "非单调",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        if (!managing) {
            TextButton(onClick = onRename, contentPadding = PaddingValues(horizontal = 6.dp)) {
                Text("改名", style = MaterialTheme.typography.labelSmall)
            }
            TextButton(onClick = onDelete) { Text("删除", style = MaterialTheme.typography.labelSmall) }
        }
    }
}

@Composable
private fun RenameDialog(
    title: String,
    initial: String,
    canReset: Boolean,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    var text by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column {
                OutlinedTextField(
                    value = text,
                    onValueChange = { if (it.length <= 40) text = it },
                    singleLine = true,
                    label = { Text("新名称（最多 40 字）") },
                    modifier = Modifier.fillMaxWidth(),
                )
                if (canReset) {
                    Text(
                        text = "留空保存 = 恢复原名。只改显示名，曲线、参数、机身名都不动。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(text) }) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun TrashDialog(
    items: List<RecipeStore.Item>,
    onDismiss: () -> Unit,
    onRestore: (RecipeStore.Item) -> Unit,
    onPurge: (RecipeStore.Item) -> Unit,
    onPurgeAll: () -> Unit,
) {
    var armed by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("回收站 · ${items.size} 条") },
        text = {
            Column {
                Text(
                    text = "恢复就回到配方列表；彻底删除不可恢复，点两次才生效。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(modifier = Modifier.height(8.dp))
                if (items.isEmpty()) {
                    Text(
                        text = "回收站是空的。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 320.dp)) {
                        items(items, key = { it.file.absolutePath }) { item ->
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = item.name,
                                        style = MaterialTheme.typography.bodySmall,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    Text(
                                        text = recipeMeta(item),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                                TextButton(onClick = { onRestore(item) }) { Text("恢复") }
                                TextButton(onClick = {
                                    if (armed == item.file.absolutePath) {
                                        onPurge(item)
                                        armed = null
                                    } else {
                                        armed = item.file.absolutePath
                                    }
                                }) {
                                    Text(
                                        text = if (armed == item.file.absolutePath) "确认删除" else "彻底删除",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.error,
                                    )
                                }
                            }
                        }
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { items.forEach(onRestore) }) { Text("全部恢复") }
                        OutlinedButton(onClick = {
                            if (armed == "ALL") {
                                onPurgeAll()
                                armed = null
                            } else {
                                armed = "ALL"
                            }
                        }) {
                            Text(
                                text = if (armed == "ALL") "确认清空" else "清空回收站",
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("关闭") } },
    )
}

private fun recipeMeta(item: RecipeStore.Item): String {
    val parts = ArrayList<String>(3)
    item.deltaEMean?.let { parts.add("ΔE %.2f".format(Locale.US, it)) }
    item.source?.let { parts.add(it) }
    if (parts.isEmpty()) parts.add("影调配方")
    return parts.joinToString(" · ")
}

// -- 写入设置 ---------------------------------------------------------------

@Composable
private fun WriteCard(state: UiState, viewModel: AppViewModel) {
    val parsed = state.selectedRecipe?.parsed

    Section(title = "写入内容", subtitle = "载荷取自配方，名称与参数可改") {
        if (parsed == null) {
            Text(
                text = "先在上面选一个配方。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            CurveChart(
                lut = parsed.lut,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(112.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                Metric("黑位", "${parsed.first * 100 / Payload.LUT_MAX}%")
                Metric("白位", "${parsed.last * 100 / Payload.LUT_MAX}%")
                Metric("曲线", if (parsed.monotonic) "单调" else "非单调")
                Metric("族", "0x%02X".format(Locale.US, parsed.family))
            }
        }

        OutlinedTextField(
            value = state.draftName,
            onValueChange = viewModel::setName,
            label = { Text("机身名称") },
            singleLine = true,
            enabled = !state.busy,
            supportingText = {
                Text(
                    text = "${Payload.sanitizeName(state.draftName).length}/19 · 只能用英文、数字、符号",
                    style = MaterialTheme.typography.labelSmall,
                )
            },
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
            modifier = Modifier.fillMaxWidth(),
        )

        if (state.draftName.any { it.code > 0x7E }) {
            Text(
                text = "中文会被替换成 ?：机身名称字段只认 ASCII。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
            )
        }

        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text("同时写入参数", style = MaterialTheme.typography.bodyMedium)
                Text(
                    text = "锐化 / 清晰度 / 饱和度，跟着曲线一起进机身",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Switch(
                checked = state.paramsEnabled,
                onCheckedChange = viewModel::setParamsEnabled,
                enabled = !state.busy,
            )
        }

        if (state.paramsEnabled) {
            ParamSlider("锐化", state.sharp, -3f, 9f, !state.busy, viewModel::setSharp)
            ParamSlider("清晰度", state.clarity, -5f, 5f, !state.busy, viewModel::setClarity)
            ParamSlider("饱和度", state.saturation, -3f, 3f, !state.busy, viewModel::setSaturation)
        } else {
            Text(
                text = "只写曲线，机身原有参数保持不变。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun Metric(label: String, value: String) {
    Column {
        Text(
            text = value,
            style = MaterialTheme.typography.titleSmall,
            fontFamily = FontFamily.Monospace,
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ParamSlider(
    label: String,
    value: Float,
    min: Float,
    max: Float,
    enabled: Boolean,
    onChange: (Float) -> Unit,
) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.bodySmall)
            Spacer(modifier = Modifier.weight(1f))
            Text(
                text = "%+.0f".format(Locale.US, value),
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Slider(
            value = value.coerceIn(min, max),
            onValueChange = onChange,
            valueRange = min..max,
            // 相机菜单里这三个参数都是整数级、一步 = 1（真机回读验证：字节编码是每级 4），
            // 滑块按 0.25 步进反而造出相机表达不了的值。范围不变，只改步长。
            steps = ((max - min).toInt() - 1).coerceAtLeast(0),
            enabled = enabled,
        )
    }
}

// -- 曲线图 -----------------------------------------------------------------

@Composable
private fun CurveChart(lut: IntArray, modifier: Modifier) {
    val gridColor = MaterialTheme.colorScheme.outlineVariant
    val curveColor = MaterialTheme.colorScheme.primary

    Canvas(modifier = modifier) {
        val width = size.width
        val height = size.height
        if (width <= 0f || height <= 0f) return@Canvas

        for (i in 0..4) {
            val x = width * i / 4f
            val y = height * i / 4f
            drawLine(gridColor, Offset(x, 0f), Offset(x, height), strokeWidth = 1f)
            drawLine(gridColor, Offset(0f, y), Offset(width, y), strokeWidth = 1f)
        }

        val path = Path()
        val last = (lut.size - 1).coerceAtLeast(1)
        lut.forEachIndexed { index, value ->
            val x = width * index / last.toFloat()
            val y = height * (1f - value.toFloat() / Payload.LUT_MAX)
            if (index == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(
            path = path,
            color = curveColor,
            style = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round),
        )
    }
}

// -- 底部操作条 -------------------------------------------------------------

@Composable
private fun WriteBar(state: UiState, onWrite: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 12.dp,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = if (state.selectedRecipe == null) {
                        "还没选配方"
                    } else {
                        "${Slots.label(state.selectedSlot)} ← ${Payload.sanitizeName(state.draftName)}"
                    },
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = when {
                        state.busy -> state.busyText.ifEmpty { "处理中…" }
                        state.connected -> "相机已连接"
                        state.cameraPresent && state.permissionGranted -> "相机已检测到，没连上看运行日志"
                        state.cameraPresent -> "等待 USB 授权"
                        else -> "相机未连接"
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Button(
                onClick = onWrite,
                enabled = state.connected && state.selectedRecipe != null && !state.busy,
            ) {
                Text(if (state.busy) "处理中" else "写入")
            }
        }
    }
}

// -- 对话框 -----------------------------------------------------------------

@Composable
private fun ConfirmDialog(state: UiState, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    val slot = state.selectedSlot
    val label = Slots.label(slot)
    val info = state.slots.firstOrNull { it.slot == slot }
    val occupied = info != null && !info.empty

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("写入 $label？") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = "名称  ${Payload.sanitizeName(state.draftName)}",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    text = "配方  ${state.selectedRecipe?.name ?: "-"}",
                    style = MaterialTheme.typography.bodySmall,
                )
                if (occupied) {
                    Text(
                        text = "该槽位当前是「${slotStatusText(info)}」，会被覆盖。原内容先备份到手机，可在「备份」里恢复。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                } else {
                    Text(
                        text = "槽位为空。写入后机身影调菜单里会出现这个名称。",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Text(
                    text = "写完立刻读回，逐字节核对，不一致会明确报错。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onConfirm) { Text("写入") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )
}

@Composable
private fun ResultDialog(outcome: WriteOutcome, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = outcome.title,
                color = if (outcome.success) {
                    MaterialTheme.colorScheme.onSurface
                } else {
                    MaterialTheme.colorScheme.error
                },
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("槽位  ${outcome.slotLabel}", style = MaterialTheme.typography.bodySmall)
                Text("名称  ${outcome.name.ifEmpty { "-" }}", style = MaterialTheme.typography.bodySmall)
                Text(
                    text = outcome.detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (outcome.success) {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    } else {
                        MaterialTheme.colorScheme.error
                    },
                )
                when {
                    outcome.backupName != null -> Text(
                        text = "原内容已备份：${outcome.backupName}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )

                    outcome.backupFailed -> Text(
                        text = "原内容没能备份，无法恢复。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )

                    else -> Text(
                        text = "原槽位为空，无需备份。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("好") }
        },
    )
}

@Composable
private fun BackupsDialog(
    items: List<BackupStore.Item>,
    onDismiss: () -> Unit,
    onRestore: (BackupStore.Item) -> Unit,
    onDelete: (BackupStore.Item) -> Unit,
) {
    // 两段式确认：第一次点变成「确认删除」，再点才真删（备份不进回收站）
    var confirmId by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("本机备份") },
        text = {
            Column(
                modifier = Modifier.heightIn(max = 320.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (items.isEmpty()) {
                    Text(
                        text = "还没有备份。每次写入前都会自动把原槽位存到手机。",
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else {
                    items.take(8).forEach { item ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = "${item.slotLabel} · ${item.name ?: "无名称"}",
                                    style = MaterialTheme.typography.bodySmall,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    text = "${formatTimestamp(item.takenAt)} · ${item.size} 字节",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            TextButton(
                                onClick = { onRestore(item) },
                                enabled = item.restorable,
                            ) {
                                Text("恢复")
                            }
                            val confirming = confirmId == item.file.name
                            TextButton(
                                onClick = {
                                    if (confirming) {
                                        confirmId = null
                                        onDelete(item)
                                    } else {
                                        confirmId = item.file.name
                                    }
                                },
                            ) {
                                Text(
                                    if (confirming) "确认删除" else "删除",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = if (confirming) MaterialTheme.colorScheme.error
                                    else MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
                Text(
                    text = "只有 614 字节的备份能原样回写；36 字节的「仅参数」形态没有曲线。" +
                        "删除只是删掉这份备份文件，机身内容不受影响，删后无法再从它恢复。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("关闭") }
        },
    )
}

// -- 日志 -------------------------------------------------------------------

@Composable
private fun LogCard(log: List<String>) {
    Section(title = "运行日志", subtitle = "出问题时先看这里") {
        Text(
            text = if (log.isEmpty()) "（空）" else log.takeLast(16).joinToString("\n"),
            style = MaterialTheme.typography.bodySmall.copy(
                fontFamily = FontFamily.Monospace,
                fontSize = 11.sp,
            ),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private fun formatTimestamp(millis: Long): String =
    SimpleDateFormat("MM-dd HH:mm", Locale.US).format(Date(millis))
