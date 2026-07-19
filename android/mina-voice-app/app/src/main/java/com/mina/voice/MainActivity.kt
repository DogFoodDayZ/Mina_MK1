package com.mina.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity(), TextToSpeech.OnInitListener {
    private val uiScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(12, TimeUnit.SECONDS)
        .build()
    private lateinit var tts: TextToSpeech

    private var recognizer: SpeechRecognizer? = null
    private var shouldKeepListening = false
    private val restartHandler = Handler(Looper.getMainLooper())

    private val logs = mutableStateListOf<String>()
    private var partialText by mutableStateOf("")
    private var apiBaseUrl by mutableStateOf(DEFAULT_API_URL)
    private var requireWakeWord by mutableStateOf(true)
    private var speakingEnabled by mutableStateOf(true)
    private var isListening by mutableStateOf(false)

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startContinuousListening()
        } else {
            appendLog("Permission denied: RECORD_AUDIO")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        apiBaseUrl = loadApiUrl(this)
        tts = TextToSpeech(this, this)

        setContent {
            MaterialTheme {
                MinaVoiceScreen()
            }
        }
    }

    override fun onInit(status: Int) {
        tts.language = Locale.US
    }

    override fun onDestroy() {
        super.onDestroy()
        stopListening()
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

        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {}
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}

                    override fun onError(error: Int) {
                        isListening = false
                        if (shouldKeepListening) {
                            restartHandler.postDelayed({ beginListeningSession() }, 650)
                        }
                    }

                    override fun onResults(results: Bundle?) {
                        isListening = false
                        val heard = results
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull()
                            ?.trim()
                            .orEmpty()
                        partialText = ""
                        if (heard.isNotBlank()) {
                            processTranscript(heard)
                        }
                        if (shouldKeepListening) {
                            restartHandler.postDelayed({ beginListeningSession() }, 350)
                        }
                    }

                    override fun onPartialResults(partialResults: Bundle?) {
                        partialText = partialResults
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull()
                            .orEmpty()
                    }

                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
        }

        beginListeningSession()
    }

    private fun beginListeningSession() {
        val r = recognizer ?: return
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
        }
        isListening = true
        r.startListening(intent)
    }

    private fun stopListening() {
        shouldKeepListening = false
        isListening = false
        restartHandler.removeCallbacksAndMessages(null)
        recognizer?.cancel()
        recognizer?.destroy()
        recognizer = null
        partialText = ""
    }

    private fun processTranscript(spoken: String) {
        val normalized = spoken.trim()
        appendLog("You: $normalized")

        var command = normalized
        if (requireWakeWord) {
            val lower = normalized.lowercase(Locale.US)
            val wakeIndex = lower.indexOf("hey mina")
            val altWakeIndex = lower.indexOf("mina")
            if (wakeIndex >= 0) {
                command = normalized.substring(wakeIndex + "hey mina".length).trim()
            } else if (altWakeIndex >= 0) {
                command = normalized.substring(altWakeIndex + "mina".length).trim()
            } else {
                appendLog("Wake word not detected")
                return
            }
        }

        if (command.isBlank()) {
            appendLog("Wake word heard, waiting for command")
            return
        }

        sendToMina(command)
    }

    private fun sendToMina(command: String) {
        ioScope.launch {
            val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
            val payload = JSONObject()
                .put("input", command)
                .put("speak_response", false)
                .put("input_source", "voice")
                .toString()

            val request = Request.Builder()
                .url(normalizedBase + "/process")
                .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()

            try {
                httpClient.newCall(request).execute().use { response ->
                    val body = response.body?.string().orEmpty()
                    val json = runCatching { JSONObject(body) }.getOrNull()
                    val reply = json?.optString("reply")?.takeIf { it.isNotBlank() }
                        ?: "(No reply)"
                    appendLog("Mina: $reply")
                    if (speakingEnabled) {
                        tts.speak(reply, TextToSpeech.QUEUE_FLUSH, null, "mina-reply")
                    }
                }
            } catch (e: Exception) {
                appendLog("Error: ${e.message}")
            }
        }
    }

    private fun checkApiStatus(candidateUrl: String) {
        ioScope.launch {
            val normalizedBase = normalizeApiBaseUrl(candidateUrl)
            val request = Request.Builder()
                .url(normalizedBase + "/status")
                .get()
                .build()

            try {
                httpClient.newCall(request).execute().use { response ->
                    val body = response.body?.string().orEmpty()
                    if (!response.isSuccessful) {
                        appendLog("API check failed (${response.code}) at $normalizedBase")
                        return@use
                    }
                    val statusJson = runCatching { JSONObject(body) }.getOrNull()
                    val model = statusJson?.optString("active_model").orEmpty()
                    if (model.isNotBlank()) {
                        appendLog("API reachable at $normalizedBase (model: $model)")
                    } else {
                        appendLog("API reachable at $normalizedBase")
                    }
                }
            } catch (e: Exception) {
                appendLog("API check error: ${e.message}")
            }
        }
    }

    private fun normalizeApiBaseUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/')
        if (trimmed.isEmpty()) {
            return DEFAULT_API_URL
        }
        val lower = trimmed.lowercase(Locale.US)
        return if (lower.startsWith("http://") || lower.startsWith("https://")) {
            trimmed
        } else {
            "http://$trimmed"
        }
    }

    @Composable
    private fun MinaVoiceScreen() {
        var apiDraft by remember { mutableStateOf(apiBaseUrl) }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Text("Mina Android Voice Link", style = MaterialTheme.typography.headlineSmall)

            OutlinedTextField(
                value = apiDraft,
                onValueChange = { apiDraft = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("MK1 API URL") },
                singleLine = true
            )
            Text(
                "Tailscale examples: http://your-node-name:8000 or https://your-node-name.your-tailnet.ts.net",
                style = MaterialTheme.typography.bodySmall
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    apiBaseUrl = normalizeApiBaseUrl(apiDraft)
                    apiDraft = apiBaseUrl
                    saveApiUrl(this@MainActivity, apiBaseUrl)
                    appendLog("Saved API URL: $apiBaseUrl")
                }) {
                    Text("Save API")
                }

                Button(onClick = {
                    checkApiStatus(apiDraft)
                }) {
                    Text("Check API")
                }
            }

            Button(onClick = {
                if (isListening) stopListening() else ensureAudioPermissionAndStart()
            }) {
                Text(if (isListening) "Stop Listening" else "Start Listening")
            }

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Require 'Hey Mina'")
                    Switch(checked = requireWakeWord, onCheckedChange = { requireWakeWord = it })
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Speak Replies")
                    Switch(checked = speakingEnabled, onCheckedChange = { speakingEnabled = it })
                }
            }

            if (partialText.isNotBlank()) {
                Text("Heard: $partialText")
            }

            Spacer(Modifier.height(4.dp))
            Text("Activity Log")
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(logs) { line -> Text(line, style = MaterialTheme.typography.bodySmall) }
            }
        }
    }

    companion object {
        private const val PREFS = "mina_voice_prefs"
        private const val KEY_API_URL = "api_url"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"

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
    }
}
