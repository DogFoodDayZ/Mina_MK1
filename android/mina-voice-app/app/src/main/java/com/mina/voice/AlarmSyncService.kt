package com.mina.voice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class AlarmSyncService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .build()
    private var pollJob: Job? = null
    private var alarmRingtone: Ringtone? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
        startForeground(SYNC_NOTIFICATION_ID, buildSyncNotification("Watching Mina alarms"))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!isAlarmSyncEnabled(this)) {
            stopSelf()
            return START_NOT_STICKY
        }

        if (pollJob?.isActive != true) {
            pollJob = scope.launch {
                while (isActive) {
                    pollDueAlarms()
                    delay(POLL_INTERVAL_MS)
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        pollJob?.cancel()
        alarmRingtone?.stop()
        alarmRingtone = null
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun pollDueAlarms() {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val base = prefs.getString(KEY_API_URL, DEFAULT_API_URL)
            ?.trim()
            ?.trimEnd('/')
            .orEmpty()
        val parsedBase = base.toHttpUrlOrNull() ?: return
        if (parsedBase.host == "0.0.0.0" || parsedBase.host == "::") return

        val taskUrl = parsedBase.newBuilder()
            .encodedPath("/tasks/list")
            .query(null)
            .addQueryParameter("limit", "250")
            .build()
        val request = Request.Builder().url(taskUrl).get().build()

        try {
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return
                val payload = runCatching { JSONObject(response.body?.string().orEmpty()) }.getOrNull() ?: return
                if (!payload.optBoolean("ok", false)) return
                val tasks = payload.optJSONArray("tasks") ?: return

                for (index in 0 until tasks.length()) {
                    val task = tasks.optJSONObject(index) ?: continue
                    if (!task.optBoolean("due", false)) continue
                    if (!task.optBoolean("alarm", false) && !task.optBoolean("beep", false)) continue
                    if (task.optString("status", "pending").equals("done", ignoreCase = true)) continue

                    val taskId = task.optLong("id", -1L)
                    if (taskId <= 0L) continue
                    val dueAt = task.optDouble("due_at", 0.0)
                    val signature = "$taskId:$dueAt"
                    if (!markAlarmUnseen(signature)) continue

                    val text = task.optString("text", "Mina alarm").ifBlank { "Mina alarm" }
                    ringPhoneAlarm(taskId, text)
                }
            }
        } catch (_: Exception) {
            // Connectivity may come and go; the next poll retries without disturbing the app.
        }
    }

    private fun markAlarmUnseen(signature: String): Boolean {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val seen = prefs.getStringSet(KEY_SEEN_ALARMS, emptySet())?.toMutableSet() ?: mutableSetOf()
        if (seen.contains(signature)) return false
        if (seen.size >= MAX_SEEN_ALARMS) seen.clear()
        seen.add(signature)
        prefs.edit().putStringSet(KEY_SEEN_ALARMS, seen).apply()
        return true
    }

    private fun ringPhoneAlarm(taskId: Long, text: String) {
        val openAppIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            taskId.hashCode(),
            openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, ALARM_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("Mina alarm")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .build()

        getSystemService(NotificationManager::class.java)
            .notify(ALARM_NOTIFICATION_BASE + (taskId % 10000L).toInt(), notification)

        alarmRingtone?.stop()
        val alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        alarmRingtone = RingtoneManager.getRingtone(this, alarmUri)?.apply {
            audioAttributes = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                isLooping = true
            }
            play()
        }

        scope.launch {
            delay(ALARM_SOUND_DURATION_MS)
            alarmRingtone?.stop()
            alarmRingtone = null
        }
    }

    private fun createNotificationChannels() {
        val notificationManager = getSystemService(NotificationManager::class.java)
        val syncChannel = NotificationChannel(
            SYNC_CHANNEL_ID,
            "Mina alarm sync",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Keeps Mina phone alarms synchronized"
            setSound(null, null)
        }
        val alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
        val alarmChannel = NotificationChannel(
            ALARM_CHANNEL_ID,
            "Mina alarms",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Due alarms from Mina"
            enableVibration(true)
            setSound(
                alarmUri,
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
        }
        notificationManager.createNotificationChannels(listOf(syncChannel, alarmChannel))
    }

    private fun buildSyncNotification(status: String) =
        NotificationCompat.Builder(this, SYNC_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("Mina phone alarms")
            .setContentText(status)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    companion object {
        const val PREFS = "mina_voice_prefs"
        const val KEY_API_URL = "api_url"
        const val KEY_ALARM_SYNC_ENABLED = "alarm_sync_enabled"
        private const val KEY_SEEN_ALARMS = "seen_phone_alarm_signatures"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"
        private const val SYNC_CHANNEL_ID = "mina_alarm_sync"
        private const val ALARM_CHANNEL_ID = "mina_due_alarms"
        private const val SYNC_NOTIFICATION_ID = 4101
        private const val ALARM_NOTIFICATION_BASE = 4200
        private const val POLL_INTERVAL_MS = 15_000L
        private const val ALARM_SOUND_DURATION_MS = 20_000L
        private const val MAX_SEEN_ALARMS = 500

        fun isAlarmSyncEnabled(context: Context): Boolean =
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getBoolean(KEY_ALARM_SYNC_ENABLED, true)

        fun setAlarmSyncEnabled(context: Context, enabled: Boolean) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_ALARM_SYNC_ENABLED, enabled)
                .apply()
        }

        fun start(context: Context) {
            if (!isAlarmSyncEnabled(context)) return
            androidx.core.content.ContextCompat.startForegroundService(
                context,
                Intent(context, AlarmSyncService::class.java),
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, AlarmSyncService::class.java))
        }
    }
}