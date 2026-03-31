import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var sessionStore: SessionStore
    @State private var baseURL = ""
    @State private var email = ""
    @State private var password = ""
    @State private var message = "Ready."
    @State private var isLoading = false
    @State private var history: [AttendanceEvent] = []

    var body: some View {
        Group {
            if sessionStore.isAuthenticated {
                attendanceView
            } else {
                loginView
            }
        }
        .padding(28)
        .background(
            LinearGradient(
                colors: [Color(red: 0.96, green: 0.98, blue: 0.99), Color(red: 0.91, green: 0.95, blue: 0.98)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .onAppear {
            if baseURL.isEmpty {
                baseURL = sessionStore.baseURL
            }
        }
    }

    private var loginView: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Pranathi Employee")
                .font(.system(size: 30, weight: .bold, design: .rounded))

            Text("Native macOS client for employee login and attendance.")
                .foregroundStyle(.secondary)

            formField(title: "Backend URL") {
                TextField("https://your-app.azurewebsites.net", text: bindingForBaseURL)
                    .textFieldStyle(.roundedBorder)
            }

            formField(title: "Email") {
                TextField("employee@contoso.com", text: $email)
                    .textFieldStyle(.roundedBorder)
            }

            formField(title: "Password") {
                SecureField("Password", text: $password)
                    .textFieldStyle(.roundedBorder)
            }

            HStack(spacing: 12) {
                Button("Login") {
                    Task { await authenticate(mode: .login) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isLoading)

                Button("Sign Up") {
                    Task { await authenticate(mode: .signup) }
                }
                .buttonStyle(.bordered)
                .disabled(isLoading)
            }

            statusRow
            Spacer()
        }
        .frame(maxWidth: 560, maxHeight: .infinity, alignment: .topLeading)
    }

    private var attendanceView: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(sessionStore.email)
                .font(.system(size: 28, weight: .bold, design: .rounded))

            Text("Organization: \(sessionStore.organization.uppercased()) | \(sessionStore.baseURL)")
                .foregroundStyle(.secondary)

            HStack(spacing: 12) {
                Button("Punch In") {
                    Task { await sendPunch("punch_in") }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isLoading)

                Button("Punch Out") {
                    Task { await sendPunch("punch_out") }
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(isLoading)

                Button("Refresh") {
                    Task { await loadHistory() }
                }
                .buttonStyle(.bordered)
                .disabled(isLoading)

                Button("Logout") {
                    Task { await logout() }
                }
                .buttonStyle(.bordered)
                .disabled(isLoading)
            }

            statusRow

            Text("Recent attendance")
                .font(.title3.bold())
                .padding(.top, 6)

            List(history) { item in
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.eventType.replacingOccurrences(of: "_", with: " ").uppercased())
                        .font(.headline)
                    Text(item.requestedAt ?? "No timestamp")
                        .font(.subheadline)
                    Text("\(item.status) | \(item.source)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
            .overlay {
                if history.isEmpty {
                    ContentUnavailableView(
                        "No Attendance Yet",
                        systemImage: "clock.badge.questionmark",
                        description: Text("Punch in or refresh after your first event is processed.")
                    )
                }
            }
        }
        .task {
            if history.isEmpty {
                await loadHistory()
            }
        }
    }

    private var statusRow: some View {
        HStack(spacing: 10) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
            }
            Text(message)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var bindingForBaseURL: Binding<String> {
        Binding(
            get: { baseURL },
            set: { baseURL = $0 }
        )
    }

    private func formField<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            content()
        }
    }

    private func authenticate(mode: AuthenticationMode) async {
        guard !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !email.isEmpty, !password.isEmpty else {
            message = "Backend URL, email, and password are required."
            return
        }

        sessionStore.saveBaseURL(baseURL)

        isLoading = true
        message = mode == .signup ? "Creating account..." : "Signing in..."

        defer { isLoading = false }

        do {
            let client = APIClient(baseURL: sessionStore.baseURL)
            let response = try await {
                switch mode {
                case .login:
                    return try await client.login(email: email, password: password)
                case .signup:
                    return try await client.signup(email: email, password: password)
                }
            }()

            sessionStore.saveSession(
                token: response.token,
                email: response.user.email,
                organization: response.user.organization
            )
            message = response.message ?? "Success."
            await loadHistory()
        } catch {
            message = error.localizedDescription
        }
    }

    private func sendPunch(_ action: String) async {
        isLoading = true
        message = action == "punch_in" ? "Sending punch in..." : "Sending punch out..."

        defer { isLoading = false }

        do {
            let client = APIClient(baseURL: sessionStore.baseURL)
            message = try await client.punch(token: sessionStore.token, action: action)
            await loadHistory()
        } catch {
            message = error.localizedDescription
        }
    }

    private func loadHistory() async {
        guard sessionStore.isAuthenticated else {
            history = []
            return
        }

        isLoading = true
        if history.isEmpty {
            message = "Loading attendance history..."
        }

        defer { isLoading = false }

        do {
            let client = APIClient(baseURL: sessionStore.baseURL)
            history = try await client.history(token: sessionStore.token)
            message = "History refreshed."
        } catch {
            message = error.localizedDescription
        }
    }

    private func logout() async {
        isLoading = true
        defer { isLoading = false }

        do {
            let client = APIClient(baseURL: sessionStore.baseURL)
            try await client.logout(token: sessionStore.token)
        } catch {
            message = error.localizedDescription
        }

        history = []
        email = ""
        password = ""
        sessionStore.clearSession()
        message = "Logged out."
    }
}

private enum AuthenticationMode {
    case login
    case signup
}
