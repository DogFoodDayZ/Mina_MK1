package com.mina.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity(), TextToSpeech.OnInitListener {
    private val minaReadableColors: ColorScheme = lightColorScheme(
        primary = Color(0xFF2D6CDF),
        onPrimary = Color(0xFFFFFFFF),
        background = Color(0xFFF5F7FB),
        onBackground = Color(0xFF111827),
        surface = Color(0xFFFFFFFF),
        onSurface = Color(0xFF111827),
        outline = Color(0xFF7C8AA3),
    )

    private val uiScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(12, TimeUnit.SECONDS)
        .build()
    private lateinit var tts: TextToSpeech
    private lateinit var audioManager: AudioManager
    private lateinit var speechFocusRequest: AudioFocusRequest
    private var speechPlayer: MediaPlayer? = null
    private var speechFile: File? = null
    private var speechSynthesisId: String? = null

    private var recognizer: SpeechRecognizer? = null
    private var shouldKeepListening = false
    private val restartHandler = Handler(Looper.getMainLooper())
    private var restartScheduled = false
    private var startListeningPending = false
    private var pausedForTextInput = false
    private var ttsReady = false
    private var ttsSpeaking = false
    private var suppressListeningUntilMs = 0L
    private var lastPartialCandidate = ""
    private var wakeWordArmedUntilMs = 0L

    private val logs = mutableStateListOf<String>()
    private var partialText by mutableStateOf("")
    private var apiBaseUrl by mutableStateOf(DEFAULT_API_URL)
    private var requireWakeWord by mutableStateOf(true)
    private var speakingEnabled by mutableStateOf(true)
    private var isListening by mutableStateOf(false)
    private var listeningEnabled by mutableStateOf(false)
    private var drivingModeEnabled by mutableStateOf(false)
    private var phoneAlarmsEnabled by mutableStateOf(true)
    private var drivingCheckinMinutes by mutableStateOf(20)
    private var showRepliesPanel by mutableStateOf(false)
    private var drivingCheckinWatcherJob: Job? = null
    private var lastDrivingDueSignature: String? = null
    private var lastDrivingDueTriggeredAtMs: Long = 0L

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startContinuousListening()
        } else {
            appendLog("Permission denied: RECORD_AUDIO")
        }
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        appendLog(if (granted) "Phone alarm notifications enabled" else "Phone alarm notifications denied")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        apiBaseUrl = loadApiUrl(this)
        drivingModeEnabled = loadDrivingModeEnabled(this)
        drivingCheckinMinutes = loadDrivingCheckinMinutes(this)
        phoneAlarmsEnabled = AlarmSyncService.isAlarmSyncEnabled(this)
        audioManager = getSystemService(AudioManager::class.java)
        speechFocusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(speechAudioAttributes())
            .setOnAudioFocusChangeListener { }
            .setWillPauseWhenDucked(false)
            .build()
        tts = TextToSpeech(this, this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (phoneAlarmsEnabled) {
            AlarmSyncService.start(this)
        }
        if (drivingModeEnabled) {
            startDrivingCheckinWatcher()
        }

        setContent {
            MaterialTheme(colorScheme = minaReadableColors) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    MinaVoiceScreen()
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        if (drivingModeEnabled) {
            startDrivingCheckinWatcher()
        }
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.US
            tts.setAudioAttributes(speechAudioAttributes())
            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {
                    ttsSpeaking = true
                    suppressListeningUntilMs = System.currentTimeMillis() + TTS_RESUME_DELAY_MS
                }

                override fun onDone(utteranceId: String?) {
                    if (utteranceId == speechSynthesisId) {
                        playSynthesizedSpeech()
                    }
                }

                override fun onError(utteranceId: String?) {
                    if (utteranceId == speechSynthesisId) {
                        finishSpeechPlayback()
                    }
                }
            })
            ttsReady = true
        } else {
            ttsReady = false
            appendLog("TTS init failed: $status")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        stopListening()
        stopDrivingCheckinWatcher()
        speechPlayer?.release()
        speechPlayer = null
        speechFile?.delete()
        speechFile = null
        abandonSpeechAudioFocus()
        tts.stop()
        tts.shutdown()
        uiScope.cancel()
        ioScope.cancel()
    }

    private fun appendLog(line: String) {
        uiScope.launch {
            logs.add(0, line)
            if (logs.size > 120) {
                logs.removeLast()
            }
        }
    }

    private fun runOnMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            block()
        } else {
            restartHandler.post { block() }
        }
    }

    private fun safeUiAction(tag: String, action: () -> Unit) {
        try {
            action()
        } catch (t: Throwable) {
            Log.e("MinaVoice", "UI action failed: $tag", t)
            appendLog("Action failed ($tag): ${t.message}")
        }
    }

    private fun safeSpeak(text: String, utteranceId: String) {
        if (!speakingEnabled || text.isBlank()) return
        runOnMain {
            if (!ttsReady) {
                appendLog("TTS not ready")
                return@runOnMain
            }
            try {
                // Pause recognizer around TTS output to reduce self-triggering.
                ttsSpeaking = true
                suppressListeningUntilMs = System.currentTimeMillis() + TTS_RESUME_DELAY_MS
                if (isListening) {
                    recognizer?.cancel()
                    isListening = false
                }
                partialText = ""
                tts.stop()
                speechPlayer?.release()
                speechPlayer = null
                speechFile?.delete()
                val outputFile = File.createTempFile("mina_tts_", ".wav", cacheDir)
                val synthesisId = "${utteranceId}_synth_${System.nanoTime()}"
                speechFile = outputFile
                speechSynthesisId = synthesisId
                val result = tts.synthesizeToFile(text, Bundle(), outputFile, synthesisId)
                if (result == TextToSpeech.ERROR) {
                    finishSpeechPlayback()
                }
            } catch (t: Throwable) {
                Log.e("MinaVoice", "TTS speak failed", t)
                appendLog("TTS error: ${t.message}")
                finishSpeechPlayback()
            }
        }
    }

    private fun playSynthesizedSpeech() {
        runOnMain {
            val outputFile = speechFile
            if (outputFile == null || !outputFile.isFile) {
                finishSpeechPlayback()
                return@runOnMain
            }
            try {
                val player = MediaPlayer().apply {
                    setAudioAttributes(speechAudioAttributes())
                    setDataSource(outputFile.absolutePath)
                    setOnPreparedListener { preparedPlayer ->
                        val focusResult = audioManager.requestAudioFocus(speechFocusRequest)
                        if (focusResult != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
                            appendLog("Spotify ducking request was not granted")
                        }
                        preparedPlayer.start()
                    }
                    setOnCompletionListener { finishSpeechPlayback() }
                    setOnErrorListener { _, _, _ ->
                        finishSpeechPlayback()
                        true
                    }
                }
                speechPlayer = player
                player.prepareAsync()
            } catch (t: Throwable) {
                Log.e("MinaVoice", "Synthesized speech playback failed", t)
                appendLog("Speech playback error: ${t.message}")
                finishSpeechPlayback()
            }
        }
    }

    private fun finishSpeechPlayback() {
        runOnMain {
            speechPlayer?.release()
            speechPlayer = null
            speechFile?.delete()
            speechFile = null
            speechSynthesisId = null
            abandonSpeechAudioFocus()
            resumeListeningAfterTts()
        }
    }

    private fun speechAudioAttributes(): AudioAttributes {
        return AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
    }

    private fun abandonSpeechAudioFocus() {
        if (::audioManager.isInitialized && ::speechFocusRequest.isInitialized) {
            audioManager.abandonAudioFocusRequest(speechFocusRequest)
        }
    }

    private fun resumeListeningAfterTts() {
        runOnMain {
            ttsSpeaking = false
            val now = System.currentTimeMillis()
            if (suppressListeningUntilMs < now) {
                suppressListeningUntilMs = now + TTS_RESUME_DELAY_MS
            }
            if (shouldKeepListening && !pausedForTextInput) {
                val delay = (suppressListeningUntilMs - now).coerceAtLeast(200L)
                queueListeningRestart(delay)
            }
        }
    }

    private fun queueListeningRestart(delayMs: Long) {
        if (!shouldKeepListening || pausedForTextInput || restartScheduled) return
        val now = System.currentTimeMillis()
        val ttsDelay = if (ttsSpeaking || now < suppressListeningUntilMs) {
            (suppressListeningUntilMs - now).coerceAtLeast(0L)
        } else {
            0L
        }
        val effectiveDelay = maxOf(delayMs, ttsDelay)
        restartScheduled = true
        restartHandler.postDelayed({
            restartScheduled = false
            beginListeningSession()
        }, effectiveDelay)
    }

    private fun setTextInputFocused(focused: Boolean) {
        if (focused) {
            if (pausedForTextInput) return
            pausedForTextInput = true
            restartHandler.removeCallbacksAndMessages(null)
            restartScheduled = false
            recognizer?.cancel()
            isListening = false
            partialText = ""
        } else {
            if (!pausedForTextInput) return
            pausedForTextInput = false
            partialText = ""
            if (shouldKeepListening) {
                queueListeningRestart(250)
            }
        }
    }

    private fun hasAudioPermission(): Boolean {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
    }

    private fun ensureAudioPermissionAndStart() {
        if (hasAudioPermission()) {
            startContinuousListening()
        } else {
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startContinuousListening() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            appendLog("Speech recognition not available on this device")
            return
        }

        shouldKeepListening = true
        listeningEnabled = true
        pausedForTextInput = false

        if (recognizer == null) {
            try {
                recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {
                        runOnMain {
                            isListening = true
                            startListeningPending = false
                        }
                    }
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}

                    override fun onError(error: Int) {
                        runOnMain {
                            isListening = false
                            startListeningPending = false
                            if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
                                shouldKeepListening = false
                                listeningEnabled = false
                                appendLog("Microphone permission is required for listening")
                                return@runOnMain
                            }

                            val restartDelay = when (error) {
                                SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> 900L
                                SpeechRecognizer.ERROR_CLIENT -> 650L
                                SpeechRecognizer.ERROR_NETWORK,
                                SpeechRecognizer.ERROR_NETWORK_TIMEOUT,
                                SpeechRecognizer.ERROR_SERVER,
                                SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> 500L
                                else -> 180L
                            }
                            queueListeningRestart(restartDelay)
                        }
                    }

                    override fun onResults(results: Bundle?) {
                        val heardCandidates = results
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.map { it.trim() }
                            ?.filter { it.isNotBlank() }
                            .orEmpty()
                        runOnMain {
                            isListening = false
                            startListeningPending = false
                            partialText = ""
                            val candidates = buildList {
                                addAll(heardCandidates)
                                if (lastPartialCandidate.isNotBlank()) {
                                    add(lastPartialCandidate)
                                }
                            }.distinct()
                            lastPartialCandidate = ""
                            if (candidates.isNotEmpty()) {
                                processTranscriptCandidates(candidates)
                            }
                            queueListeningRestart(150)
                        }
                    }

                    override fun onPartialResults(partialResults: Bundle?) {
                        val next = partialResults
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull()
                            .orEmpty()
                        runOnMain {
                            partialText = next
                            lastPartialCandidate = next
                            if (containsWakeWord(next)) {
                                wakeWordArmedUntilMs = System.currentTimeMillis() + WAKE_WORD_ARM_WINDOW_MS
                            }
                        }
                    }

                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
            } catch (t: Throwable) {
                Log.e("MinaVoice", "Recognizer init failed", t)
                appendLog("Recognizer init error: ${t.message}")
                shouldKeepListening = false
                return
            }
        }

        beginListeningSession()
    }

    private fun beginListeningSession() {
        if (!shouldKeepListening || pausedForTextInput || startListeningPending || isListening) {
            return
        }
        val now = System.currentTimeMillis()
        if (ttsSpeaking || now < suppressListeningUntilMs) {
            queueListeningRestart((suppressListeningUntilMs - now).coerceAtLeast(220L))
            return
        }
        val r = recognizer ?: return
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.US.toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, Locale.US.toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 1800L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1800L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 2600L)
        }
        startListeningPending = true
        try {
            isListening = true
            r.startListening(intent)
            startListeningPending = false
        } catch (e: Exception) {
            isListening = false
            startListeningPending = false
            appendLog("Recognizer start error: ${e.message}")
            queueListeningRestart(800)
        }
    }

    private fun stopListening() {
        shouldKeepListening = false
        listeningEnabled = false
        pausedForTextInput = false
        startListeningPending = false
        restartScheduled = false
        isListening = false
        restartHandler.removeCallbacksAndMessages(null)
        recognizer?.cancel()
        recognizer?.destroy()
        recognizer = null
        partialText = ""
        lastPartialCandidate = ""
        wakeWordArmedUntilMs = 0L
    }

    private fun processTranscriptCandidates(candidates: List<String>) {
        val primary = candidates.first().trim()
        appendLog("You: $primary")

        if (!requireWakeWord) {
            sendToMina(primary)
            return
        }

        val command = candidates
            .asSequence()
            .mapNotNull { extractCommandAfterWakeWord(it) }
            .firstOrNull()

        if (!command.isNullOrBlank()) {
            wakeWordArmedUntilMs = 0L
            sendToMina(command)
            return
        }

        val now = System.currentTimeMillis()
        val wakeOnly = candidates.any { containsWakeWord(it) }
        if (wakeOnly) {
            wakeWordArmedUntilMs = now + WAKE_WORD_ARM_WINDOW_MS
            appendLog("Wake word heard - listening for command")
            return
        }

        if (now <= wakeWordArmedUntilMs) {
            wakeWordArmedUntilMs = 0L
            sendToMina(primary)
            return
        }

        appendLog("Wake word not detected")
    }

    private fun containsWakeWord(spoken: String): Boolean {
        val tokens = Regex("[a-z0-9']+")
            .findAll(spoken.lowercase(Locale.US))
            .map { it.value }
            .toList()
        return findWakeTokenIndex(tokens) >= 0
    }

    private fun extractCommandAfterWakeWord(spoken: String): String? {
        val cleaned = spoken.trim().lowercase(Locale.US)
        if (cleaned.isBlank()) return null

        val directPhrases = listOf("hey mina", "hi mina", "okay mina", "ok mina", "mina")
        for (phrase in directPhrases) {
            val idx = cleaned.indexOf(phrase)
            if (idx >= 0) {
                val candidate = spoken.substring((idx + phrase.length).coerceAtMost(spoken.length)).trim()
                if (candidate.isNotBlank()) return candidate
            }
        }

        val tokenRegex = Regex("[a-z0-9']+")
        val tokens = tokenRegex.findAll(cleaned).map { it.value }.toList()
        if (tokens.isEmpty()) return null

        val wakeIdx = findWakeTokenIndex(tokens)
        if (wakeIdx < 0 || wakeIdx >= tokens.lastIndex) {
            return null
        }

        val commandTokens = tokens.drop(wakeIdx + 1)
        val command = commandTokens.joinToString(" ").trim()
        return command.ifBlank { null }
    }

    private fun findWakeTokenIndex(tokens: List<String>): Int {
        for (i in tokens.indices) {
            val token = tokens[i]
            if (token in WAKE_WORD_ALIASES) {
                return i
            }

            val nearMina = looksLikeMinaToken(token)
            if (!nearMina) continue

            val prev = if (i > 0) tokens[i - 1] else ""
            if (i == 0 || prev in WAKE_PREFIXES) {
                return i
            }
        }
        return -1
    }

    private fun looksLikeMinaToken(token: String): Boolean {
        if (token.length !in 3..6) return false
        if (token == "mine") return false
        if (token.endsWith("mina") || token.endsWith("nima")) return true
        return editDistance(token, "mina") <= 1
    }

    private fun editDistance(a: String, b: String): Int {
        if (a == b) return 0
        if (a.isEmpty()) return b.length
        if (b.isEmpty()) return a.length

        val prev = IntArray(b.length + 1) { it }
        val curr = IntArray(b.length + 1)

        for (i in 1..a.length) {
            curr[0] = i
            val ca = a[i - 1]
            for (j in 1..b.length) {
                val cost = if (ca == b[j - 1]) 0 else 1
                curr[j] = minOf(
                    curr[j - 1] + 1,
                    prev[j] + 1,
                    prev[j - 1] + cost,
                )
            }
            for (j in prev.indices) {
                prev[j] = curr[j]
            }
        }
        return prev[b.length]
    }

    private fun sendToMina(command: String) {
        ioScope.launch {
            try {
                val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
                val processUrls = buildEndpointCandidates(normalizedBase, "/process")
                if (processUrls.isEmpty()) {
                    appendLog("Invalid API URL: $normalizedBase")
                    return@launch
                }
                val payload = JSONObject()
                    .put("input", command)
                    .put("speak_response", false)
                    .put("input_source", "voice")
                    .toString()

                var lastError: String? = null
                for (processUrl in processUrls) {
                    val request = Request.Builder()
                        .url(processUrl)
                        .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()

                    httpClient.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            lastError = "${response.code} @ ${processUrl.encodedPath}"
                            return@use
                        }
                        val json = runCatching { JSONObject(body) }.getOrNull()
                        val reply = json?.optString("reply")?.takeIf { it.isNotBlank() }
                            ?: "(No reply)"
                        appendLog("Mina: $reply")
                        syncDrivingModeFromVoiceCommand(command)
                        safeSpeak(reply, "mina-reply")
                        persistWorkingBaseUrlFrom(processUrl)
                        return@launch
                    }
                }

                appendLog("Mina request failed${if (lastError != null) ": $lastError" else ""}")
            } catch (e: Exception) {
                appendLog("Error: ${e.message}")
            }
        }
    }

    private fun syncDrivingModeFromVoiceCommand(command: String) {
        val lower = command.trim().lowercase(Locale.US)
        val isDrivingCommand =
            (lower.contains("driving") || lower.contains("road")) &&
                (lower.contains("check-in") || lower.contains("check in") || lower.contains("companion"))
        if (!isDrivingCommand) return

        val stopping = listOf("stop", "disable", "turn off", "cancel").any { lower.contains(it) }
        val starting = listOf("start", "enable", "turn on", "begin").any { lower.contains(it) }
        if (!stopping && !starting) return

        runOnMain {
            if (stopping) {
                drivingModeEnabled = false
                saveDrivingModeEnabled(this, false)
                stopDrivingCheckinWatcher()
            } else {
                drivingModeEnabled = true
                saveDrivingModeEnabled(this, true)
                startDrivingCheckinWatcher()
            }
        }
    }

    private suspend fun sendCompanionCommandNow(command: String, speakResult: Boolean = false): Boolean {
        try {
            val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
            val processUrls = buildEndpointCandidates(normalizedBase, "/process")
            if (processUrls.isEmpty()) {
                appendLog("Invalid API URL: $normalizedBase")
                return false
            }
            val payload = JSONObject()
                .put("input", command)
                .put("speak_response", false)
                .put("input_source", "voice")
                .toString()

            var lastError: String? = null
            for (processUrl in processUrls) {
                val request = Request.Builder()
                    .url(processUrl)
                    .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                    .build()

                httpClient.newCall(request).execute().use { response ->
                    val body = response.body?.string().orEmpty()
                    if (!response.isSuccessful) {
                        lastError = "${response.code} @ ${processUrl.encodedPath}"
                        return@use
                    }
                    val json = runCatching { JSONObject(body) }.getOrNull()
                    val reply = json?.optString("reply")?.takeIf { it.isNotBlank() }
                        ?: "(No reply)"
                    appendLog("Mina: $reply")
                    if (speakResult) {
                        safeSpeak(reply, "mina-companion")
                    }
                    persistWorkingBaseUrlFrom(processUrl)
                    return true
                }
            }

            appendLog("Companion request failed${if (lastError != null) ": $lastError" else ""}")
        } catch (e: Exception) {
            appendLog("Companion command error: ${e.message}")
        }

        return false
    }

    private fun sendCompanionCommand(command: String, speakResult: Boolean = false) {
        ioScope.launch {
            sendCompanionCommandNow(command = command, speakResult = speakResult)
        }
    }

    private fun applyDrivingMode(enable: Boolean, minutes: Int) {
        val safeMinutes = minutes.coerceIn(5, 180)
        drivingCheckinMinutes = safeMinutes
        saveDrivingCheckinMinutes(this, safeMinutes)

        drivingModeEnabled = enable
        saveDrivingModeEnabled(this, enable)

        if (enable) {
            appendLog("Driving mode enabled (every $safeMinutes minutes)")
            ensureAudioPermissionAndStart()
            startDrivingCheckinWatcher()
            sendCompanionCommand(
                command = "start my driving check-ins every $safeMinutes minutes",
                speakResult = true,
            )
        } else {
            appendLog("Driving mode disabled")
            stopDrivingCheckinWatcher()
            ioScope.launch {
                try {
                    val stopSent = sendCompanionCommandNow(
                        command = "stop my driving check-ins",
                        speakResult = false,
                    )
                    val cleared = clearDrivingTasksNow()
                    if (!stopSent && cleared <= 0) {
                        appendLog("Driving stop command did not confirm; no tasks cleared")
                    } else if (cleared > 0) {
                        appendLog("Driving tasks cleared: $cleared")
                    }
                } catch (e: Exception) {
                    appendLog("Driving stop fallback error: ${e.message}")
                }
            }
        }
    }

    private suspend fun clearDrivingTasksNow(): Int {
        try {
            val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
            if (isWildcardBindAddress(normalizedBase)) {
                return 0
            }

            val listUrls = buildEndpointCandidates(normalizedBase, "/tasks/list")
            if (listUrls.isEmpty()) return 0

            var responseJson: JSONObject? = null
            for (listUrl in listUrls) {
                try {
                    val urlWithLimit = listUrl.newBuilder()
                        .setQueryParameter("limit", DRIVING_CHECKIN_TASK_SCAN_LIMIT.toString())
                        .build()
                    val request = Request.Builder().url(urlWithLimit).get().build()

                    httpClient.newCall(request).execute().use { response ->
                        if (!response.isSuccessful) return@use
                        val body = response.body?.string().orEmpty()
                        responseJson = runCatching { JSONObject(body) }.getOrNull()
                        if (responseJson != null) {
                            persistWorkingBaseUrlFrom(listUrl)
                        }
                    }
                    if (responseJson != null) {
                        break
                    }
                } catch (_: Exception) {
                    // Try next candidate URL; this fallback must never crash the app.
                    continue
                }
            }

            val payload = responseJson ?: return 0
            val tasks = payload.optJSONArray("tasks") ?: return 0
            var cleared = 0

            for (i in 0 until tasks.length()) {
                val task = tasks.optJSONObject(i) ?: continue
                val status = task.optString("status", "pending").lowercase(Locale.US)
                if (status == "done") continue

                val tagsJson = task.optJSONArray("tags")
                val tags = mutableSetOf<String>()
                if (tagsJson != null) {
                    for (j in 0 until tagsJson.length()) {
                        val tag = tagsJson.optString(j, "").trim().lowercase(Locale.US)
                        if (tag.isNotBlank()) {
                            tags.add(tag)
                        }
                    }
                }

                val text = task.optString("text", "")
                val lower = text.lowercase(Locale.US)
                val isDriving = tags.contains("driving_checkin") ||
                    lower.contains("driving check-in") ||
                    (lower.contains("driving") && lower.contains("check"))
                if (!isDriving) continue

                val taskId = task.optInt("id", -1)
                if (taskId <= 0) continue

                val markUrls = buildEndpointCandidates(normalizedBase, "/tasks/mark_done")
                if (markUrls.isEmpty()) continue

                var marked = false
                for (markUrl in markUrls) {
                    try {
                        val urlWithTask = markUrl.newBuilder()
                            .setQueryParameter("task_id", taskId.toString())
                            .build()
                        val request = Request.Builder()
                            .url(urlWithTask)
                            .post("".toRequestBody("text/plain".toMediaType()))
                            .build()

                        httpClient.newCall(request).execute().use { response ->
                            if (!response.isSuccessful) return@use
                            val body = response.body?.string().orEmpty()
                            val json = runCatching { JSONObject(body) }.getOrNull()
                            if (json?.optBoolean("ok", false) == true) {
                                persistWorkingBaseUrlFrom(markUrl)
                                marked = true
                            }
                        }
                    } catch (_: Exception) {
                        continue
                    }

                    if (marked) {
                        break
                    }
                }

                if (marked) {
                    cleared += 1
                }
            }

            return cleared
        } catch (_: Exception) {
            return 0
        }
    }

    private fun startDrivingCheckinWatcher() {
        if (!drivingModeEnabled) return
        if (drivingCheckinWatcherJob?.isActive == true) return

        drivingCheckinWatcherJob = ioScope.launch {
            appendLog("Driving check-in watcher active")
            while (isActive && drivingModeEnabled) {
                try {
                    pollAndTriggerDrivingCheckins()
                } catch (e: Exception) {
                    appendLog("Driving watcher error: ${e.message}")
                }
                delay(DRIVING_CHECKIN_POLL_MS)
            }
        }
    }

    private fun stopDrivingCheckinWatcher() {
        drivingCheckinWatcherJob?.cancel()
        drivingCheckinWatcherJob = null
        appendLog("Driving check-in watcher stopped")
    }

    private suspend fun pollAndTriggerDrivingCheckins() {
        val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
        if (isWildcardBindAddress(normalizedBase)) {
            return
        }

        val listUrls = buildEndpointCandidates(normalizedBase, "/tasks/list")
        if (listUrls.isEmpty()) return

        var responseJson: JSONObject? = null
        for (listUrl in listUrls) {
            val urlWithLimit = listUrl.newBuilder()
                .setQueryParameter("limit", DRIVING_CHECKIN_TASK_SCAN_LIMIT.toString())
                .build()
            val request = Request.Builder().url(urlWithLimit).get().build()

            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@use
                val body = response.body?.string().orEmpty()
                responseJson = runCatching { JSONObject(body) }.getOrNull()
                if (responseJson != null) {
                    persistWorkingBaseUrlFrom(listUrl)
                    return@pollAndTriggerDrivingCheckins
                }
            }
        }

        val payload = responseJson ?: return
        val tasks = payload.optJSONArray("tasks") ?: return

        for (i in 0 until tasks.length()) {
            val task = tasks.optJSONObject(i) ?: continue
            val due = task.optBoolean("due", false)
            if (!due) continue

            val tagsJson = task.optJSONArray("tags")
            val tags = mutableSetOf<String>()
            if (tagsJson != null) {
                for (j in 0 until tagsJson.length()) {
                    val tag = tagsJson.optString(j, "").trim().lowercase(Locale.US)
                    if (tag.isNotBlank()) {
                        tags.add(tag)
                    }
                }
            }

            val text = task.optString("text", "")
            val lower = text.lowercase(Locale.US)
            val isDriving = tags.contains("driving_checkin") ||
                lower.contains("driving check-in") ||
                (lower.contains("driving") && lower.contains("check"))
            if (!isDriving) continue

            val taskId = task.optInt("id", -1)
            val dueAt = task.optDouble("due_at", 0.0)
            val signature = "$taskId:$dueAt"
            val nowMs = System.currentTimeMillis()

            if (
                signature == lastDrivingDueSignature &&
                (nowMs - lastDrivingDueTriggeredAtMs) < DRIVING_CHECKIN_TRIGGER_COOLDOWN_MS
            ) {
                continue
            }

            lastDrivingDueSignature = signature
            lastDrivingDueTriggeredAtMs = nowMs
            appendLog("Driving check-in due -> prompting Mina")
            val sent = sendCompanionCommandNow(
                command = "Do my due driving check-in reminder now.",
                speakResult = true,
            )
            if (!sent) {
                // Local fallback so one failed network call does not drop the reminder entirely.
                safeSpeak(
                    "Driving check-in. Quick reset: breathe, hydrate, and stay focused.",
                    "driving-checkin-fallback",
                )
            }
            return
        }
    }

    private fun checkApiStatus(candidateUrl: String) {
        ioScope.launch {
            try {
                val normalizedBase = normalizeApiBaseUrl(candidateUrl)
                if (isWildcardBindAddress(normalizedBase)) {
                    appendLog("0.0.0.0 cannot be reached from phone. Use your host IP, e.g. 100.x.x.x:8000 or 192.168.x.x:8000")
                    return@launch
                }
                val statusUrls = buildEndpointCandidates(normalizedBase, "/status")
                if (statusUrls.isEmpty()) {
                    appendLog("Invalid API URL: $normalizedBase")
                    return@launch
                }

                var lastFailure: String? = null
                for (statusUrl in statusUrls) {
                    val request = Request.Builder()
                        .url(statusUrl)
                        .get()
                        .build()

                    httpClient.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            lastFailure = "${response.code} @ ${statusUrl.encodedPath}"
                            return@use
                        }
                        val statusJson = runCatching { JSONObject(body) }.getOrNull()
                        val model = statusJson?.optString("active_model").orEmpty()
                        val activeBase = apiBaseFromEndpoint(statusUrl)
                        if (model.isNotBlank()) {
                            appendLog("API reachable at $activeBase (model: $model)")
                        } else {
                            appendLog("API reachable at $activeBase")
                        }
                        persistWorkingBaseUrl(activeBase)
                        return@launch
                    }
                }

                appendLog("API check failed${if (lastFailure != null) ": $lastFailure" else ""}")
            } catch (e: Exception) {
                appendLog("API check error: ${e.message}")
            }
        }
    }

    private fun requestRemoteRestart() {
        ioScope.launch {
            try {
                val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
                if (isWildcardBindAddress(normalizedBase)) {
                    appendLog("Remote restart blocked: use a real host IP instead of 0.0.0.0")
                    return@launch
                }

                val restartUrls = buildEndpointCandidates(normalizedBase, "/system/restart")
                if (restartUrls.isEmpty()) {
                    appendLog("Remote restart failed: invalid API URL")
                    return@launch
                }

                appendLog("Requesting remote restart...")

                var accepted = false
                var lastError: String? = null

                for (restartUrl in restartUrls) {
                    val restartWithParams = restartUrl.newBuilder()
                        .setQueryParameter("delay_ms", "1000")
                        .build()

                    val request = Request.Builder()
                        .url(restartWithParams)
                        .post("{}".toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()

                    httpClient.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            lastError = "${response.code} @ ${restartWithParams.encodedPath}"
                            return@use
                        }

                        val json = runCatching { JSONObject(body) }.getOrNull()
                        val ok = json?.optBoolean("ok", false) ?: false
                        if (!ok) {
                            val apiErr = json?.optString("error")?.takeIf { it.isNotBlank() }
                            lastError = apiErr ?: "restart_rejected"
                            return@use
                        }

                        accepted = true
                        appendLog("Remote restart accepted. Waiting for API to come back...")
                        persistWorkingBaseUrlFrom(restartUrl)
                        return@use
                    }

                    if (accepted) break
                }

                if (!accepted) {
                    appendLog("Remote restart failed${if (lastError != null) ": $lastError" else ""}")
                    return@launch
                }

                var restored = false
                repeat(16) {
                    delay(2000)
                    val statusUrls = buildEndpointCandidates(normalizedBase, "/status")
                    for (statusUrl in statusUrls) {
                        val statusRequest = Request.Builder().url(statusUrl).get().build()
                        httpClient.newCall(statusRequest).execute().use { response ->
                            if (response.isSuccessful) {
                                restored = true
                                appendLog("API is back online after remote restart")
                                persistWorkingBaseUrlFrom(statusUrl)
                                return@launch
                            }
                        }
                    }
                }

                if (!restored) {
                    appendLog("Restart requested, but API is still offline. Use Check API in a few seconds.")
                }
            } catch (e: Exception) {
                appendLog("Remote restart error: ${e.message}")
            }
        }
    }

    private fun buildEndpointCandidates(base: String, endpoint: String): List<HttpUrl> {
        val out = mutableListOf<HttpUrl>()
        val direct = (base.trimEnd('/') + endpoint).toHttpUrlOrNull()
        if (direct != null) out.add(direct)

        val parsedBase = base.toHttpUrlOrNull()
        if (parsedBase != null) {
            val rootBased = parsedBase.newBuilder()
                .encodedPath(endpoint)
                .query(null)
                .fragment(null)
                .build()
            if (out.none { it.toString() == rootBased.toString() }) {
                out.add(rootBased)
            }
        }

        return out
    }

    private fun apiBaseFromEndpoint(url: HttpUrl): String {
        return url.newBuilder()
            .encodedPath("/")
            .query(null)
            .fragment(null)
            .build()
            .toString()
            .trimEnd('/')
    }

    private fun persistWorkingBaseUrlFrom(endpointUrl: HttpUrl) {
        persistWorkingBaseUrl(apiBaseFromEndpoint(endpointUrl))
    }

    private fun persistWorkingBaseUrl(base: String) {
        if (base.isBlank() || base == apiBaseUrl) return
        apiBaseUrl = base
        saveApiUrl(this, base)
        appendLog("Using API URL: $base")
    }

    private fun normalizeApiBaseUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/', '.', ',', ';')
        if (trimmed.isEmpty()) {
            return DEFAULT_API_URL
        }

        val dottedPort = Regex("""^(\d{1,3}(?:\.\d{1,3}){3})\.(\d{2,5})(/.*)?$""")
        val fixedDottedPort = dottedPort.replace(trimmed) { m ->
            val host = m.groupValues[1]
            val port = m.groupValues[2]
            val tail = m.groupValues.getOrElse(3) { "" }
            "$host:$port$tail"
        }

        val lower = fixedDottedPort.lowercase(Locale.US)
        val hasScheme = lower.startsWith("http://") || lower.startsWith("https://")
        val withScheme = if (hasScheme) fixedDottedPort else "http://$fixedDottedPort"
        if (withScheme.toHttpUrlOrNull() != null) {
            return withScheme
        }

        // Recover common typo form: IPv6-like hosts entered without [] brackets.
        val scheme = if (lower.startsWith("https://")) "https" else "http"
        val rest = if (hasScheme) fixedDottedPort.substringAfter("://") else fixedDottedPort
        val slashIdx = rest.indexOf('/')
        val authority = if (slashIdx >= 0) rest.substring(0, slashIdx) else rest
        val tail = if (slashIdx >= 0) rest.substring(slashIdx) else ""

        if (!authority.startsWith("[") && authority.count { it == ':' } >= 2) {
            val lastColon = authority.lastIndexOf(':')
            val hostPart = authority.substring(0, lastColon)
            val portPart = authority.substring(lastColon + 1)
            val repairedAuthority = if (portPart.all { it.isDigit() }) {
                "[$hostPart]:$portPart"
            } else {
                "[$authority]"
            }
            val repaired = "$scheme://$repairedAuthority$tail"
            if (repaired.toHttpUrlOrNull() != null) {
                return repaired.trimEnd('/')
            }
        }

        return withScheme
    }

    private fun isWildcardBindAddress(base: String): Boolean {
        val host = base.toHttpUrlOrNull()?.host?.lowercase(Locale.US) ?: return false
        return host == "0.0.0.0" || host == "::" || host == "[::]"
    }

    @Composable
    private fun MinaVoiceScreen() {
        var apiDraft by remember { mutableStateOf(apiBaseUrl) }
        var driveMinutesDraft by remember { mutableStateOf(drivingCheckinMinutes.toString()) }
        val minaReplies = logs.filter { it.startsWith("Mina:") }.take(80)

        Box(modifier = Modifier.fillMaxSize()) {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                item {
                    Text("Mina Android Voice Link", style = MaterialTheme.typography.headlineSmall)
                }

                item {
                    OutlinedTextField(
                        value = apiDraft,
                        onValueChange = { apiDraft = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .onFocusChanged { setTextInputFocused(it.isFocused) },
                        label = { Text("MK1 API URL") },
                        singleLine = true
                    )
                }

                item {
                    Text(
                        "Use: http://phone-or-lan-ip:8000",
                        style = MaterialTheme.typography.bodySmall
                    )
                }

                item {
                    Column(
                        modifier = Modifier.fillMaxWidth(),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                safeUiAction("save_api") {
                                    val normalized = normalizeApiBaseUrl(apiDraft)
                                    if (isWildcardBindAddress(normalized)) {
                                        appendLog("0.0.0.0 is a server bind address, not a phone destination. Use your host IP.")
                                        return@safeUiAction
                                    }
                                    if (buildEndpointCandidates(normalized, "/status").isEmpty()) {
                                        appendLog("Invalid API URL: $normalized")
                                        return@safeUiAction
                                    }
                                    apiBaseUrl = normalized
                                    apiDraft = apiBaseUrl
                                    saveApiUrl(this@MainActivity, apiBaseUrl)
                                    appendLog("Saved API URL: $apiBaseUrl")
                                }
                            }
                        ) {
                            Text("Save API")
                        }

                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                safeUiAction("check_api") {
                                    checkApiStatus(apiDraft)
                                }
                            }
                        ) {
                            Text("Check API")
                        }

                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                safeUiAction("remote_restart") {
                                    requestRemoteRestart()
                                }
                            }
                        ) {
                            Text("Remote Restart Core")
                        }
                    }
                }

                item {
                    OutlinedTextField(
                        value = driveMinutesDraft,
                        onValueChange = { driveMinutesDraft = it.filter { ch -> ch.isDigit() }.take(3) },
                        label = { Text("Check-in every (minutes)") },
                        singleLine = true,
                        modifier = Modifier
                            .fillMaxWidth()
                            .onFocusChanged { setTextInputFocused(it.isFocused) }
                    )
                }

                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Driving Mode", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = drivingModeEnabled,
                            onCheckedChange = { enabled ->
                                safeUiAction("toggle_driving_mode") {
                                    val parsed = driveMinutesDraft.toIntOrNull() ?: drivingCheckinMinutes
                                    val safe = parsed.coerceIn(5, 180)
                                    driveMinutesDraft = safe.toString()
                                    applyDrivingMode(enabled, safe)
                                }
                            }
                        )
                    }
                }

                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Phone Alarms", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = phoneAlarmsEnabled,
                            onCheckedChange = { enabled ->
                                phoneAlarmsEnabled = enabled
                                AlarmSyncService.setAlarmSyncEnabled(this@MainActivity, enabled)
                                if (enabled) {
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                                        ContextCompat.checkSelfPermission(
                                            this@MainActivity,
                                            Manifest.permission.POST_NOTIFICATIONS,
                                        ) != PackageManager.PERMISSION_GRANTED
                                    ) {
                                        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                                    }
                                    AlarmSyncService.start(this@MainActivity)
                                    appendLog("Phone alarm watcher enabled")
                                } else {
                                    AlarmSyncService.stop(this@MainActivity)
                                    appendLog("Phone alarm watcher disabled")
                                }
                            },
                        )
                    }
                }

                item {
                    Button(
                        modifier = Modifier.fillMaxWidth(),
                        onClick = {
                            safeUiAction("toggle_listening") {
                                if (listeningEnabled) stopListening() else ensureAudioPermissionAndStart()
                            }
                        }
                    ) {
                        Text(if (listeningEnabled) "Stop Listening" else "Start Listening")
                    }
                }

                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Require 'Hey Mina'", style = MaterialTheme.typography.bodyMedium)
                        Switch(checked = requireWakeWord, onCheckedChange = { requireWakeWord = it })
                    }
                }

                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Speak Replies", style = MaterialTheme.typography.bodyMedium)
                        Switch(checked = speakingEnabled, onCheckedChange = { speakingEnabled = it })
                    }
                }

                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("Mina Replies", style = MaterialTheme.typography.bodyMedium)
                        Button(onClick = { showRepliesPanel = true }) {
                            Text("Show")
                        }
                    }
                }

                if (partialText.isNotBlank()) {
                    item {
                        Card(modifier = Modifier.fillMaxWidth()) {
                            Text(
                                modifier = Modifier.padding(12.dp),
                                text = "Heard: $partialText",
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                }
            }

            AnimatedVisibility(
                visible = showRepliesPanel,
                modifier = Modifier
                    .align(Alignment.CenterEnd)
                    .padding(8.dp),
                enter = slideInHorizontally(initialOffsetX = { fullWidth -> fullWidth }) + fadeIn(),
                exit = slideOutHorizontally(targetOffsetX = { fullWidth -> fullWidth }) + fadeOut(),
            ) {
                Card(
                    modifier = Modifier
                        .width(320.dp)
                        .fillMaxHeight()
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text("Mina Replies", style = MaterialTheme.typography.titleMedium)
                            Button(onClick = { showRepliesPanel = false }) {
                                Text("Close")
                            }
                        }

                        if (minaReplies.isEmpty()) {
                            Text("No replies yet", style = MaterialTheme.typography.bodySmall)
                        } else {
                            LazyColumn(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .fillMaxHeight(),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                items(minaReplies) { line ->
                                    Card(modifier = Modifier.fillMaxWidth()) {
                                        Text(
                                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                                            text = line,
                                            style = MaterialTheme.typography.bodySmall,
                                            maxLines = 4,
                                            overflow = TextOverflow.Ellipsis
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

        }
    }

    companion object {
        private const val PREFS = "mina_voice_prefs"
        private const val KEY_API_URL = "api_url"
        private const val KEY_DRIVING_MODE_ENABLED = "driving_mode_enabled"
        private const val KEY_DRIVING_CHECKIN_MINUTES = "driving_checkin_minutes"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"
        private val WAKE_PREFIXES = setOf("hey", "hi", "ok", "okay", "yo", "hello")
        private val WAKE_WORD_ALIASES = setOf("mina", "nima", "amina", "meena", "mena", "mini")
        private const val TTS_RESUME_DELAY_MS = 900L
        private const val WAKE_WORD_ARM_WINDOW_MS = 6000L
        private const val DRIVING_CHECKIN_POLL_MS = 45000L
        private const val DRIVING_CHECKIN_TRIGGER_COOLDOWN_MS = 90000L
        private const val DRIVING_CHECKIN_TASK_SCAN_LIMIT = 200

        private fun loadApiUrl(context: Context): String {
            return context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_API_URL, DEFAULT_API_URL)
                ?: DEFAULT_API_URL
        }

        private fun saveApiUrl(context: Context, value: String) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putString(KEY_API_URL, value)
                .apply()
        }

        private fun loadDrivingModeEnabled(context: Context): Boolean {
            return context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getBoolean(KEY_DRIVING_MODE_ENABLED, false)
        }

        private fun saveDrivingModeEnabled(context: Context, value: Boolean) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_DRIVING_MODE_ENABLED, value)
                .apply()
        }

        private fun loadDrivingCheckinMinutes(context: Context): Int {
            val v = context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getInt(KEY_DRIVING_CHECKIN_MINUTES, 20)
            return v.coerceIn(5, 180)
        }

        private fun saveDrivingCheckinMinutes(context: Context, value: Int) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putInt(KEY_DRIVING_CHECKIN_MINUTES, value.coerceIn(5, 180))
                .apply()
        }
    }
}
