package com.pranathi.employeeapp

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlin.concurrent.thread

class AttendanceActivity : AppCompatActivity() {
    private lateinit var sessionStore: SessionStore
    private lateinit var titleView: TextView
    private lateinit var subtitleView: TextView
    private lateinit var statusView: TextView
    private lateinit var historyContainer: LinearLayout
    private lateinit var punchInButton: Button
    private lateinit var punchOutButton: Button
    private lateinit var refreshButton: Button
    private lateinit var logoutButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_attendance)

        sessionStore = SessionStore(this)
        if (sessionStore.getToken().isBlank() || sessionStore.getBaseUrl().isBlank()) {
            openLoginScreen()
            return
        }

        titleView = findViewById(R.id.titleView)
        subtitleView = findViewById(R.id.subtitleView)
        statusView = findViewById(R.id.statusView)
        historyContainer = findViewById(R.id.historyContainer)
        punchInButton = findViewById(R.id.punchInButton)
        punchOutButton = findViewById(R.id.punchOutButton)
        refreshButton = findViewById(R.id.refreshButton)
        logoutButton = findViewById(R.id.logoutButton)

        titleView.text = sessionStore.getEmail()
        subtitleView.text = "Organization: ${sessionStore.getOrganization().uppercase()} | ${sessionStore.getBaseUrl()}"

        punchInButton.setOnClickListener { sendPunch("punch_in") }
        punchOutButton.setOnClickListener { sendPunch("punch_out") }
        refreshButton.setOnClickListener { loadHistory() }
        logoutButton.setOnClickListener { logout() }

        loadHistory()
    }

    private fun sendPunch(action: String) {
        setLoading(true)
        statusView.text = if (action == "punch_in") "Sending punch in..." else "Sending punch out..."

        thread {
            try {
                val apiClient = ApiClient(sessionStore.getBaseUrl())
                val result = apiClient.punch(sessionStore.getToken(), action)
                runOnUiThread {
                    statusView.text = result.body.optString("message", "Attendance updated.")
                    setLoading(false)
                    loadHistory()
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    statusView.text = exc.message ?: "Attendance request failed."
                    setLoading(false)
                }
            }
        }
    }

    private fun loadHistory() {
        statusView.text = "Loading attendance history..."
        historyContainer.removeAllViews()

        thread {
            try {
                val apiClient = ApiClient(sessionStore.getBaseUrl())
                val history = apiClient.history(sessionStore.getToken())
                runOnUiThread {
                    if (history.length() == 0) {
                        addHistoryLine("No attendance events have been processed yet.")
                    } else {
                        for (index in 0 until history.length()) {
                            val item = history.getJSONObject(index)
                            val eventType = item.optString("event_type").replace("_", " ")
                            val requestedAt = item.optString("requested_at")
                            val status = item.optString("status")
                            val source = item.optString("source")
                            addHistoryLine("${eventType.uppercase()} | $requestedAt | $status | $source")
                        }
                    }
                    statusView.text = "History refreshed."
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    statusView.text = exc.message ?: "Unable to load history."
                }
            }
        }
    }

    private fun logout() {
        thread {
            try {
                val apiClient = ApiClient(sessionStore.getBaseUrl())
                apiClient.logout(sessionStore.getToken())
            } catch (_: Exception) {
            } finally {
                runOnUiThread {
                    sessionStore.clearSession()
                    openLoginScreen()
                }
            }
        }
    }

    private fun addHistoryLine(text: String) {
        val row = TextView(this).apply {
            this.text = text
            textSize = 14f
            setPadding(0, 18, 0, 18)
        }
        historyContainer.addView(row)
    }

    private fun setLoading(isLoading: Boolean) {
        punchInButton.isEnabled = !isLoading
        punchOutButton.isEnabled = !isLoading
        refreshButton.isEnabled = !isLoading
        logoutButton.isEnabled = !isLoading
        findViewById<View>(R.id.progressBar).visibility = if (isLoading) View.VISIBLE else View.GONE
    }

    private fun openLoginScreen() {
        startActivity(Intent(this, LoginActivity::class.java))
        finish()
    }
}
