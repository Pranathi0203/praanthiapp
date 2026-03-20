package com.pranathi.employeeapp

import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class ApiResult(val statusCode: Int, val body: JSONObject)

class ApiException(message: String) : Exception(message)

class ApiClient(private val baseUrl: String) {
    fun signup(email: String, password: String): ApiResult {
        return sendJson(
            path = "/api/mobile/signup",
            method = "POST",
            payload = JSONObject()
                .put("email", email)
                .put("password", password),
        )
    }

    fun login(email: String, password: String): ApiResult {
        return sendJson(
            path = "/api/mobile/login",
            method = "POST",
            payload = JSONObject()
                .put("email", email)
                .put("password", password),
        )
    }

    fun me(token: String): ApiResult {
        return sendJson(path = "/api/mobile/me", method = "GET", token = token)
    }

    fun history(token: String): JSONArray {
        val result = sendJson(path = "/api/mobile/attendance/history", method = "GET", token = token)
        return result.body.optJSONArray("history") ?: JSONArray()
    }

    fun punch(token: String, action: String): ApiResult {
        return sendJson(
            path = "/api/mobile/attendance/punch",
            method = "POST",
            token = token,
            payload = JSONObject().put("action", action),
        )
    }

    fun logout(token: String): ApiResult {
        return sendJson(path = "/api/mobile/logout", method = "POST", token = token)
    }

    private fun sendJson(
        path: String,
        method: String,
        token: String? = null,
        payload: JSONObject? = null,
    ): ApiResult {
        val normalizedBaseUrl = baseUrl.trim().trimEnd('/')
        val connection = (URL("$normalizedBaseUrl$path").openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 15000
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Content-Type", "application/json")
            if (!token.isNullOrBlank()) {
                setRequestProperty("Authorization", "Bearer $token")
            }
            doInput = true
        }

        if (payload != null) {
            connection.doOutput = true
            OutputStreamWriter(connection.outputStream).use { writer ->
                writer.write(payload.toString())
            }
        }

        val responseCode = connection.responseCode
        val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
        val responseText = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
        val json = if (responseText.isBlank()) JSONObject() else JSONObject(responseText)

        if (responseCode !in 200..299) {
            val detail = json.optString("detail").ifBlank { "Request failed with status $responseCode." }
            throw ApiException(detail)
        }

        return ApiResult(responseCode, json)
    }
}
