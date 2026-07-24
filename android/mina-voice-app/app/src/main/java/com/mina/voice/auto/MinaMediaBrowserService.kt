package com.mina.voice.auto

import android.os.Bundle
import android.support.v4.media.MediaBrowserCompat
import android.support.v4.media.MediaDescriptionCompat
import android.support.v4.media.MediaMetadataCompat
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import androidx.media.MediaBrowserServiceCompat
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class MinaMediaBrowserService : MediaBrowserServiceCompat() {
    private lateinit var mediaSession: MediaSessionCompat
    private val worker = Executors.newSingleThreadExecutor()
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .writeTimeout(8, TimeUnit.SECONDS)
        .build()

    override fun onCreate() {
        super.onCreate()

        mediaSession = MediaSessionCompat(this, "MinaMediaSession")
        mediaSession.setFlags(
            MediaSessionCompat.FLAG_HANDLES_MEDIA_BUTTONS or
                MediaSessionCompat.FLAG_HANDLES_TRANSPORT_CONTROLS
        )
        mediaSession.setCallback(object : MediaSessionCompat.Callback() {
            override fun onPlay() {
                mediaSession.isActive = true
                runAction(MEDIA_ID_STATUS)
            }

            override fun onPause() {
                updatePlaybackState(PlaybackStateCompat.STATE_PAUSED)
            }

            override fun onStop() {
                updatePlaybackState(PlaybackStateCompat.STATE_STOPPED)
                mediaSession.isActive = false
            }

            override fun onPlayFromMediaId(mediaId: String?, extras: Bundle?) {
                mediaSession.isActive = true
                runAction(mediaId ?: MEDIA_ID_STATUS)
            }
        })

        setNowPlaying("Mina Voice", "Ready in Android Auto")
        updatePlaybackState(PlaybackStateCompat.STATE_PAUSED)
        sessionToken = mediaSession.sessionToken
    }

    override fun onDestroy() {
        worker.shutdownNow()
        mediaSession.release()
        super.onDestroy()
    }

    override fun onGetRoot(
        clientPackageName: String,
        clientUid: Int,
        rootHints: Bundle?
    ): BrowserRoot {
        return BrowserRoot(ROOT_ID, null)
    }

    override fun onLoadChildren(
        parentId: String,
        result: Result<MutableList<MediaBrowserCompat.MediaItem>>
    ) {
        val items = mutableListOf<MediaBrowserCompat.MediaItem>()

        if (parentId == ROOT_ID) {
            items += playableItem(
                mediaId = MEDIA_ID_TALK_TO_MINA,
                title = "Talk to Mina",
                subtitle = "Send a quick hello command"
            )
            items += playableItem(
                mediaId = MEDIA_ID_STATUS,
                title = "Check Mina Status",
                subtitle = "Verify MK1 API reachability"
            )
        }

        result.sendResult(items)
    }

    private fun playableItem(mediaId: String, title: String, subtitle: String): MediaBrowserCompat.MediaItem {
        val description = MediaDescriptionCompat.Builder()
            .setMediaId(mediaId)
            .setTitle(title)
            .setSubtitle(subtitle)
            .build()

        return MediaBrowserCompat.MediaItem(description, MediaBrowserCompat.MediaItem.FLAG_PLAYABLE)
    }

    private fun runAction(mediaId: String) {
        val actionLabel = when (mediaId) {
            MEDIA_ID_TALK_TO_MINA -> "Talk to Mina"
            MEDIA_ID_STATUS -> "Checking Status"
            else -> "Running"
        }
        setNowPlaying(actionLabel, "Working...")
        updatePlaybackState(PlaybackStateCompat.STATE_BUFFERING)

        worker.execute {
            val resultText = when (mediaId) {
                MEDIA_ID_TALK_TO_MINA -> sendProcessCommand("Say hello from Android Auto and confirm you are connected.")
                MEDIA_ID_STATUS -> checkStatus()
                else -> "Unknown action"
            }

            val title = when (mediaId) {
                MEDIA_ID_TALK_TO_MINA -> "Mina Reply"
                MEDIA_ID_STATUS -> "Mina Status"
                else -> "Mina"
            }

            setNowPlaying(title, resultText)
            updatePlaybackState(PlaybackStateCompat.STATE_PAUSED)
        }
    }

    private fun setNowPlaying(title: String, subtitle: String) {
        val metadata = MediaMetadataCompat.Builder()
            .putString(MediaMetadataCompat.METADATA_KEY_TITLE, title)
            .putString(MediaMetadataCompat.METADATA_KEY_ARTIST, subtitle.take(120))
            .build()
        mediaSession.setMetadata(metadata)
    }

    private fun checkStatus(): String {
        val base = normalizeApiBaseUrl(loadApiUrl())
        if (isLoopbackBase(base)) {
            return "Set API URL in phone app (not 127.0.0.1)"
        }
        val statusUrl = base.trimEnd('/') + "/status"
        val request = Request.Builder().url(statusUrl).get().build()

        return try {
            httpClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return "Unreachable (${response.code})"
                }
                val json = runCatching { JSONObject(body) }.getOrNull()
                val model = json?.optString("active_model").orEmpty()
                if (model.isNotBlank()) "Online ($model)" else "Online"
            }
        } catch (e: Exception) {
            "Unreachable (${e.message ?: "error"})"
        }
    }

    private fun sendProcessCommand(command: String): String {
        val base = normalizeApiBaseUrl(loadApiUrl())
        if (isLoopbackBase(base)) {
            return "Set API URL in phone app first"
        }
        val processUrl = base.trimEnd('/') + "/process"
        val payload = JSONObject()
            .put("input", command)
            .put("speak_response", false)
            .put("input_source", "android_auto_media")
            .toString()

        val request = Request.Builder()
            .url(processUrl)
            .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()

        return try {
            httpClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return "Request failed (${response.code})"
                }

                val json = runCatching { JSONObject(body) }.getOrNull()
                json?.optString("reply")?.takeIf { it.isNotBlank() } ?: "No reply"
            }
        } catch (e: Exception) {
            "Request error (${e.message ?: "error"})"
        }
    }

    private fun loadApiUrl(): String {
        return getSharedPreferences(PREFS, MODE_PRIVATE)
            .getString(KEY_API_URL, DEFAULT_API_URL)
            ?: DEFAULT_API_URL
    }

    private fun normalizeApiBaseUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/', '.', ',', ';')
        if (trimmed.isEmpty()) return DEFAULT_API_URL

        val dottedPort = Regex("""^(\d{1,3}(?:\.\d{1,3}){3})\.(\d{2,5})(/.*)?$""")
        val fixed = dottedPort.replace(trimmed) { m ->
            val host = m.groupValues[1]
            val port = m.groupValues[2]
            val tail = m.groupValues.getOrElse(3) { "" }
            "$host:$port$tail"
        }

        val lower = fixed.lowercase(Locale.US)
        return if (lower.startsWith("http://") || lower.startsWith("https://")) fixed else "http://$fixed"
    }

    private fun isLoopbackBase(base: String): Boolean {
        val lower = base.lowercase(Locale.US)
        return lower.contains("127.0.0.1") || lower.contains("localhost") || lower.contains("0.0.0.0")
    }

    private fun updatePlaybackState(state: Int) {
        val playbackState = PlaybackStateCompat.Builder()
            .setActions(
                PlaybackStateCompat.ACTION_PLAY or
                    PlaybackStateCompat.ACTION_PAUSE or
                    PlaybackStateCompat.ACTION_STOP or
                    PlaybackStateCompat.ACTION_PLAY_PAUSE
            )
            .setState(state, PlaybackStateCompat.PLAYBACK_POSITION_UNKNOWN, 1.0f)
            .build()
        mediaSession.setPlaybackState(playbackState)
    }

    companion object {
        private const val ROOT_ID = "mina_root"
        private const val MEDIA_ID_TALK_TO_MINA = "mina_talk"
        private const val MEDIA_ID_STATUS = "mina_status"
        private const val PREFS = "mina_voice_prefs"
        private const val KEY_API_URL = "api_url"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"
    }
}
