import Combine
import Foundation

@MainActor
final class SessionStore: ObservableObject {
    @Published var baseURL: String
    @Published var token: String
    @Published var email: String
    @Published var organization: String

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.baseURL = defaults.string(forKey: Keys.baseURL) ?? ""
        self.token = defaults.string(forKey: Keys.token) ?? ""
        self.email = defaults.string(forKey: Keys.email) ?? ""
        self.organization = defaults.string(forKey: Keys.organization) ?? ""
    }

    var isAuthenticated: Bool {
        !token.isEmpty && !baseURL.isEmpty
    }

    func saveBaseURL(_ value: String) {
        let normalized = normalizeBaseURL(value)
        baseURL = normalized
        defaults.set(normalized, forKey: Keys.baseURL)
    }

    func saveSession(token: String, email: String, organization: String) {
        self.token = token
        self.email = email
        self.organization = organization

        defaults.set(token, forKey: Keys.token)
        defaults.set(email, forKey: Keys.email)
        defaults.set(organization, forKey: Keys.organization)
    }

    func clearSession() {
        token = ""
        email = ""
        organization = ""

        defaults.removeObject(forKey: Keys.token)
        defaults.removeObject(forKey: Keys.email)
        defaults.removeObject(forKey: Keys.organization)
    }
}

private func normalizeBaseURL(_ value: String) -> String {
    var normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    while normalized.hasSuffix("/") {
        normalized.removeLast()
    }
    return normalized
}

private enum Keys {
    static let baseURL = "pranathi_employee_macos.base_url"
    static let token = "pranathi_employee_macos.token"
    static let email = "pranathi_employee_macos.email"
    static let organization = "pranathi_employee_macos.organization"
}
