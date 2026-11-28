import Foundation
import Testing
import YabStore

@MainActor
struct AppleModelAccessTests {
    private func withDefaults(_ run: (UserDefaults, String) throws -> Void) throws {
        let suite = "apple-model-access-" + UUID().uuidString
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        try run(defaults, suite)
    }

    @Test func currentChoicePrecedesHardwareAvailability() throws {
        try withDefaults { defaults, _ in
            var reads = 0
            func available() -> Bool { reads += 1; return true }
            #expect(AppleModelAccess.permits(in: defaults, availability: available))
            #expect(reads == 1)
            AppleModelAccess.setEnabled(false, in: defaults)
            #expect(!AppleModelAccess.permits(in: defaults, availability: available))
            #expect(reads == 1, "Disable must take effect before a cached hardware answer is read")
            AppleModelAccess.setEnabled(true, in: defaults)
            #expect(AppleModelAccess.permits(in: defaults, availability: available))
            #expect(reads == 2)
            #expect(!AppleModelAccess.permits(in: defaults) { false })
        }
    }

    @Test func persistenceDoesNotChangeOtherModelsOrMailPreference() throws {
        try withDefaults { defaults, suite in
            defaults.set("codex", forKey: "fixture.provider")
            defaults.set(false, forKey: "mail.sense")
            #expect(AppleModelAccess.enabled(in: defaults))
            AppleModelAccess.setEnabled(false, in: defaults)
            let reopened = try #require(UserDefaults(suiteName: suite))
            #expect(!AppleModelAccess.enabled(in: reopened))
            #expect(reopened.string(forKey: "fixture.provider") == "codex")
            #expect(reopened.object(forKey: "mail.sense") as? Bool == false)
            AppleModelAccess.setEnabled(true, in: reopened)
            #expect(AppleModelAccess.enabled(in: defaults))
        }
    }
}
