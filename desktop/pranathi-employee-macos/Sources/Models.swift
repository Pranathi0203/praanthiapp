import Foundation

struct EmployeeUser: Codable {
    let email: String
    let organization: String
}

struct AuthResponse: Codable {
    let token: String
    let user: EmployeeUser
    let message: String?
}

struct AttendanceEvent: Codable, Identifiable {
    let eventType: String
    let requestedAt: String?
    let processedAt: String?
    let status: String
    let source: String

    var id: String {
        "\(eventType)-\(requestedAt ?? UUID().uuidString)-\(status)"
    }

    enum CodingKeys: String, CodingKey {
        case eventType = "event_type"
        case requestedAt = "requested_at"
        case processedAt = "processed_at"
        case status
        case source
    }
}

struct AttendanceHistoryResponse: Codable {
    let history: [AttendanceEvent]
}

struct APIErrorResponse: Codable {
    let detail: String?
}
