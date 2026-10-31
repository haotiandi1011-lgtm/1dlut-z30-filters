package com.z30tone.writer.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Slots
import com.z30tone.writer.data.BackupStore
import com.z30tone.writer.data.RecipeStore
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

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) viewModel.importRecipe(uri)
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
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Z30 影调", style = MaterialTheme.typography.titleLarge)
                        Text(
                            text = "写入端 · 只搬曲线，不做拟合",
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
                SlotCard(state = state, onSelect = viewModel::setSlot)
            }
            item {
                RecipeCard(
                    state = state,
                    onImport = { picker.launch(arrayOf("*/*")) },
                    onSelect = viewModel::selectRecipe,
                    onDelete = viewModel::deleteRecipe,
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
            style = MaterialTheme.typography.bodySmall,
            color = textColor,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

private fun slotStatusText(info: SlotInfo?): String = when {
    info == null -> "未读取"
    info.empty -> "空"
    info.full -> info.name?.ifBlank { "无名称" } ?: "614 字节"
    else -> "${info.size} 字节 · 仅参数"
}

// -- 配方 -------------------------------------------------------------------

@Composable
private fun RecipeCard(
    state: UiState,
    onImport: () -> Unit,
    onSelect: (RecipeStore.Item) -> Unit,
    onDelete: (RecipeStore.Item) -> Unit,
) {
    Section(
        title = "配方",
        subtitle = "电脑端拟合后导出 .z30，再导入这里",
        trailing = {
            TextButton(onClick = onImport, enabled = !state.busy) { Text("导入") }
        },
    ) {
        if (state.recipes.isEmpty()) {
            Text(
                text = "还没有配方。点右上角「导入」，选一个 .z30 文件。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                state.recipes.forEach { item ->
                    RecipeRow(
                        item = item,
                        selected = state.selectedRecipe?.file?.absolutePath == item.file.absolutePath,
                        onClick = { onSelect(item) },
                        onDelete = { onDelete(item) },
                    )
                }
            }
        }
    }
}

@Composable
private fun RecipeRow(
    item: RecipeStore.Item,
    selected: Boolean,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
    val background = if (selected) {
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
        CurveChart(lut = item.parsed.lut, modifier = Modifier.size(42.dp, 32.dp))
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
        TextButton(onClick = onDelete) { Text("删除", style = MaterialTheme.typography.labelSmall) }
    }
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
                text = "%+.2f".format(Locale.US, value),
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Slider(
            value = value.coerceIn(min, max),
            onValueChange = onChange,
            valueRange = min..max,
            steps = (((max - min) / 0.25f).toInt() - 1).coerceAtLeast(0),
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
) {
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
                        }
                    }
                }
                Text(
                    text = "只有 614 字节的备份能原样回写；36 字节的「仅参数」形态没有曲线。",
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
