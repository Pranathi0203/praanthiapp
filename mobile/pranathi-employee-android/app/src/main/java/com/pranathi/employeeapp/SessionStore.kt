package com.pranathi.employeeapp

import android.content.Context

class SessionStore(context: Context) {
    private val prefs = context.getSharedPreferences("pranathi_employee_app", Context.MODE_PRIVATE)

    fun saveBaseUrl(baseUrl: String) {
        prefs.edit().putString("base_url", baseUrl.trim().trimEnd('/')).apply()
    }

    fun getBaseUrl(): String {
        return prefs.getString("base_url", "") ?: ""
    }

    fun saveSession(token: String, email: String, organization: String) {
        prefs.edit()
            .putString("token", token)
            .putString("email", email)
            .putString("organization", organization)
            .apply()
    }

    fun getToken(): String {
        return prefs.getString("token", "") ?: ""
    }

    fun getEmail(): String {
        return prefs.getString("email", "") ?: ""
    }

    fun getOrganization(): String {
        return prefs.getString("organization", "") ?: ""
    }

    fun clearSession() {
        prefs.edit()
            .remove("token")
            .remove("email")
            .remove("organization")
            .apply()
    }
}
