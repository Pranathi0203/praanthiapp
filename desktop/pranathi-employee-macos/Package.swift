// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "PranathiEmployeeMacOS",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(
            name: "PranathiEmployeeMacOS",
            targets: ["PranathiEmployeeMacOS"]
        ),
    ],
    targets: [
        .executableTarget(
            name: "PranathiEmployeeMacOS",
            path: "Sources"
        ),
    ]
)
