import Foundation
import Testing

// Temporary, for the full-suite stall experiment on a fork's CI. Not for main.
// With YAB_CI_PREFLIGHT=<path>, writes the test host's own arguments and
// environment there as JSON, so the experiment can start the host the way
// this toolchain's `swift test` starts it (and be its parent, to read its
// exit status). Without it the test does nothing; trials skip it.
struct ZZCIPreflightTests {
    @Test func hostInvocation() throws {
        guard let path = ProcessInfo.processInfo.environment["YAB_CI_PREFLIGHT"], !path.isEmpty else { return }
        let info = ProcessInfo.processInfo
        let data = try JSONSerialization.data(withJSONObject: ["arguments": info.arguments, "environment": info.environment, "pid": Int(info.processIdentifier)], options: [.prettyPrinted, .sortedKeys])
        try data.write(to: URL(fileURLWithPath: path))
    }
}
