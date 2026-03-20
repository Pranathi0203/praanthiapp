package com.pranathi.employeeapp

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlin.concurrent.thread

class LoginActivity : AppCompatActivity() {
    private lateinit var sessionStore: SessionStore
    private lateinit var baseUrlInput: EditText
    private lateinit var emailInput: EditText
    private lateinit var passwordInput: EditText
    private lateinit var messageView: TextView
    private lateinit var loginButton: Button
    private lateinit var signupButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_login)

        sessionStore = SessionStore(this)
        baseUrlInput = findViewById(R.id.baseUrlInput)
        emailInput = findViewById(R.id.emailInput)
        passwordInput = findViewById(R.id.passwordInput)
        messageView = findViewById(R.id.messageView)
        loginButton = findViewById(R.id.loginButton)
        signupButton = findViewById(R.id.signupButton)

        baseUrlInput.setText(sessionStore.getBaseUrl())

        if (sessionStore.getToken().isNotBlank() && sessionStore.getBaseUrl().isNotBlank()) {
            openAttendanceScreen()
            return
        }

        loginButton.setOnClickListener {
            authenticate(mode = "login")
        }

        signupButton.setOnClickListener {
            authenticate(mode = "signup")
        }
    }

    private fun authenticate(mode: String) {
        val baseUrl = baseUrlInput.text.toString().trim()
        val email = emailInput.text.toString().trim()
        val password = passwordInput.text.toString()

        if (baseUrl.isBlank() || email.isBlank() || password.isBlank()) {
            messageView.text = "Backend URL, email, and password are required."
            return
        }

        setLoading(true)
        sessionStore.saveBaseUrl(baseUrl)
        messageView.text = if (mode == "signup") "Creating account..." else "Signing in..."

        thread {
            try {
                val apiClient = ApiClient(baseUrl)
                val result = if (mode == "signup") {
                    apiClient.signup(email, password)
                } else {
                    apiClient.login(email, password)
                }

                val token = result.body.getString("token")
                val user = result.body.getJSONObject("user")
                sessionStore.saveSession(
                    token = token,
                    email = user.getString("email"),
                    organization = user.getString("organization"),
                )

                runOnUiThread {
                    messageView.text = result.body.optString("message", "Success.")
                    openAttendanceScreen()
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    messageView.text = exc.message ?: "Unable to contact backend."
                    setLoading(false)
                }
            }
        }
    }

    private fun setLoading(isLoading: Boolean) {
        loginButton.isEnabled = !isLoading
        signupButton.isEnabled = !isLoading
    }

    private fun openAttendanceScreen() {
        startActivity(Intent(this, AttendanceActivity::class.java))
        finish()
    }
}
