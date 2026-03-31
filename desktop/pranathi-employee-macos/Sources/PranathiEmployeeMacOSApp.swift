import SwiftUI

@main
struct PranathiEmployeeMacOSApp: App {
    @StateObject private var sessionStore = SessionStore()

    var body: some Scene {
        WindowGroup("Pranathi Employee") {
            ContentView()
                .environmentObject(sessionStore)
                .frame(minWidth: 840, minHeight: 620)
        }
        .windowResizability(.contentMinSize)
    }
}
