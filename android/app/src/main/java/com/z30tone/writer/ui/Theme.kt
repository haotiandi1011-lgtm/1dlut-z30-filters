package com.z30tone.writer.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

private val LightColors = lightColorScheme(
    primary = Color(0xFF8A5A2B),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFF3E3CE),
    onPrimaryContainer = Color(0xFF3A2410),
    secondary = Color(0xFF5A6350),
    onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFFBF9F5),
    onBackground = Color(0xFF1C1A17),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1C1A17),
    surfaceVariant = Color(0xFFF1ECE3),
    onSurfaceVariant = Color(0xFF5C554B),
    outline = Color(0xFF9A9184),
    outlineVariant = Color(0xFFE4DCD0),
    error = Color(0xFFA33A2B),
    onError = Color(0xFFFFFFFF),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFDCA871),
    onPrimary = Color(0xFF2A1A08),
    primaryContainer = Color(0xFF4A3520),
    onPrimaryContainer = Color(0xFFF6E3CC),
    secondary = Color(0xFFB6C0AB),
    onSecondary = Color(0xFF232B1D),
    background = Color(0xFF141312),
    onBackground = Color(0xFFEDE7DD),
    surface = Color(0xFF1E1C19),
    onSurface = Color(0xFFEDE7DD),
    surfaceVariant = Color(0xFF2A2723),
    onSurfaceVariant = Color(0xFFBDB4A6),
    outline = Color(0xFF6E675C),
    outlineVariant = Color(0xFF3A3630),
    error = Color(0xFFE08A78),
    onError = Color(0xFF2A0F08),
)

private val AppTypography: Typography = Typography().let { base ->
    base.copy(
        headlineSmall = base.headlineSmall.copy(
            fontFamily = FontFamily.Serif,
            fontWeight = FontWeight.SemiBold,
        ),
        titleLarge = base.titleLarge.copy(
            fontFamily = FontFamily.Serif,
            fontWeight = FontWeight.SemiBold,
        ),
        titleMedium = base.titleMedium.copy(fontWeight = FontWeight.Medium),
    )
}

@Composable
fun Z30Theme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        typography = AppTypography,
        content = content,
    )
}
