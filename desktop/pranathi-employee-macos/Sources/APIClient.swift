import Foundation

struct APIClient {
    let baseURL: String

    func signup(email: String, password: String) async throws -> AuthResponse {
        try await send(
            path: "/api/mobile/signup",
            method: "POST",
            payload: ["email": email, "password": password],
            token: nil,
            responseType: AuthResponse.self
        )
    }

    func login(email: String, password: String) async throws -> AuthResponse {
        try await send(
            path: "/api/mobile/login",
            method: "POST",
            payload: ["email": email, "password": password],
            token: nil,
            responseType: AuthResponse.self
        )
    }

    func history(token: String) async throws -> [AttendanceEvent] {
        let response: AttendanceHistoryResponse = try await send(
            path: "/api/mobile/attendance/history",
            method: "GET",
            payload: Optional<EmptyPayload>.none,
            token: token,
            responseType: AttendanceHistoryResponse.self
        )
        return response.history
    }

    func punch(token: String, action: String) async throws -> String {
        let response: JSONMessageResponse = try await send(
            path: "/api/mobile/attendance/punch",
            method: "POST",
            payload: ["action": action],
            token: token,
            responseType: JSONMessageResponse.self
        )
        return response.message ?? "Attendance updated."
    }

    func logout(token: String) async throws {
        let _: JSONMessageResponse = try await send(
            path: "/api/mobile/logout",
            method: "POST",
            payload: Optional<EmptyPayload>.none,
            token: token,
            responseType: JSONMessageResponse.self
        )
    }

    private func send<T: Decodable, P: Encodable>(
        path: String,
        method: String,
        payload: P?,
        token: String?,
        responseType: T.Type
    ) async throws -> T {
        guard let url = URL(string: normalizedBaseURL + path) else {
            throw APIClientError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        if let payload {
            request.httpBody = try JSONEncoder().encode(payload)
        }

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }

        if !(200...299).contains(httpResponse.statusCode) {
            let detail = (try? JSONDecoder().decode(APIErrorResponse.self, from: data).detail) ?? "Request failed with status \(httpResponse.statusCode)."
            throw APIClientError.server(detail)
        }

        if T.self == JSONMessageResponse.self, data.isEmpty {
            return JSONMessageResponse(message: nil) as! T
        }

        return try JSONDecoder().decode(T.self, from: data)
    }

    private var normalizedBaseURL: String {
        baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

enum APIClientError: LocalizedError {
    case invalidURL
    case invalidResponse
    case server(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "The backend URL is invalid."
        case .invalidResponse:
            return "The backend returned an invalid response."
        case .server(let message):
            return message
        }
    }
}

private struct EmptyPayload: Encodable {}

private struct JSONMessageResponse: Codable {
    let message: String?
}
