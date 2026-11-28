import Foundation
import Testing
@testable import Fast

// Temporary, for the full-suite stall experiment on a fork's CI. Not for main.
// AIUseTests on the main actor (condition B). Every run skips one of the
// two suites: --skip AIUseTestsMain for A and M, --skip AIUseTests/ for B and BM.
/// AI in Yab (register 955, #1041a, #787, 1179): one switch, On, When Asked
/// or Off, read by every AI surface through AIUse, plus Hide AI Rows in
/// Field. Each state against each surface: the field's rows and lead, the
/// entry points (⌘J, Ask About This, the chat column), background work
/// (While You Browse, mail sorting, the morning look, routines), the Mac's
/// model, the ⌘T commands and ⌘K on an Ask row, and Set up Yab's Connect row.
/// Every test is synchronous on the main actor, so the settings it sets
/// are put back before any other test reads them.
@MainActor struct AIUseTestsMain {
    // MARK: - the rule

    @Test func eachStateAllowsItsSurfaces() {
        let table: [(AIUse.Level, Bool, field: Bool, background: Bool, asked: Bool)] = [
            (.on, false, true, true, true),
            (.on, true, false, true, true),
            (.asked, false, false, false, true),
            (.asked, true, false, false, true),
            (.off, false, false, false, false),
            (.off, true, false, false, false),
        ]
        for (level, hidden, field, background, asked) in table {
            #expect(AIUse.allows(.field, level: level, rowsHidden: hidden) == field, "\(level) hidden \(hidden): field")
            #expect(AIUse.allows(.background, level: level, rowsHidden: hidden) == background, "\(level) hidden \(hidden): background")
            #expect(AIUse.allows(.asked, level: level, rowsHidden: hidden) == asked, "\(level) hidden \(hidden): asked")
        }
    }

    @Test func storedOnByDefaultAndReadBack() throws {
        let settings = try #require(UserDefaults(suiteName: "yab-ai-use-" + UUID().uuidString))
        #expect(AIUse.level(in: settings) == .on)
        #expect(!AIUse.rowsHidden(in: settings))
        for level in AIUse.Level.allCases {
            AIUse.set(level, in: settings)
            #expect(AIUse.level(in: settings) == level)
        }
        AIUse.setRowsHidden(true, in: settings)
        #expect(AIUse.rowsHidden(in: settings))
        settings.set("nonsense", forKey: AIUse.key)
        #expect(AIUse.level(in: settings) == .on)
    }

    /// #787: the Mac's model goes with the switch, for asking and for
    /// work in the background alike.
    @Test func macModelFollowsTheSwitch() throws {
        let settings = try #require(UserDefaults(suiteName: "yab-ai-mac-" + UUID().uuidString))
        let expected: [AIUse.Level: (asked: Bool, background: Bool)] = [.on: (true, true), .asked: (true, false), .off: (false, false)]
        for (level, want) in expected {
            AIUse.set(level, in: settings)
            #expect(AppleModelAccess.permits(.asked, in: settings) { true } == want.asked, "\(level): asked")
            #expect(AppleModelAccess.permits(.background, in: settings) { true } == want.background, "\(level): background")
            #expect(!AppleModelAccess.permits(.asked, in: settings) { false }, "no model, no ask")
        }
        // Its own row in Models still turns it off with AI on.
        AIUse.set(.on, in: settings)
        AppleModelAccess.setEnabled(false, in: settings)
        #expect(!AppleModelAccess.permits(.asked, in: settings) { true })
    }

    /// The morning look and replies written ahead: background, so only On.
    @Test func backgroundAgentsOnlyWhenOn() throws {
        let settings = try #require(UserDefaults(suiteName: "yab-ai-background-" + UUID().uuidString))
        BackgroundConsent.enableMorning(settings: settings)
        settings.set(true, forKey: MailConsent.aheadKey)
        settings.set(true, forKey: MailConsent.agentsKey)
        for level in AIUse.Level.allCases {
            AIUse.set(level, in: settings)
            #expect(BackgroundConsent.canStartMorning(settings: settings) == (level == .on), "\(level): morning")
            #expect(BackgroundConsent.canStartMailReply(settings: settings) == (level == .on), "\(level): mail")
            #expect((BackgroundConsent.permit(settings: settings) != nil) == (level == .on), "\(level): permit")
        }
        // A permit taken while On lapses when the switch moves.
        AIUse.set(.on, in: settings)
        let permit = try #require(BackgroundConsent.permit(settings: settings))
        AIUse.set(.asked, in: settings)
        #expect(!permit.valid)
    }

    // MARK: - in the app

    /// The level and rows set on the app's settings for one test, with an
    /// agent there and connected, the Mac's model put aside so only the
    /// agent counts, and everything put back after.
    @MainActor private func world(_ level: AIUse.Level, hidden: Bool = false, agent: Bool = true,
                                  _ body: (Browser) throws -> Void) rethrows {
        let settings = Store.settings
        let before = (level: settings.object(forKey: AIUse.key), rows: settings.object(forKey: AIUse.rowsKey),
                      mac: settings.object(forKey: AppleModelAccess.disabledKey))
        Agents.assumedInTests = agent
        Agents.connectedInTests = agent
        AppleModelAccess.setEnabled(false, in: settings)
        let id = UUID()
        let browser = Browser(windowID: id)
        browser.prefs.aiUse = level
        browser.prefs.aiRowsHidden = hidden
        defer {
            browser.dismiss()
            Agents.assumedInTests = nil
            Agents.connectedInTests = nil
            settings.set(before.level, forKey: AIUse.key)
            settings.set(before.rows, forKey: AIUse.rowsKey)
            settings.set(before.mac, forKey: AppleModelAccess.disabledKey)
            browser.prefs.aiUse = AIUse.level(in: settings)
            browser.prefs.aiRowsHidden = AIUse.rowsHidden(in: settings)
            try? FileManager.default.removeItem(at: Store.file("session-window-\(id.uuidString).json"))
        }
        try body(browser)
    }

    @MainActor @Test func fieldRowsShowOnlyWhenOnAndNotHidden() {
        let cases: [(AIUse.Level, Bool, rows: Int, lead: Suggestion.Kind)] = [
            (.on, false, 2, .task), (.on, true, 0, .search), (.asked, false, 0, .search), (.off, false, 0, .search),
        ]
        for (level, hidden, rows, lead) in cases {
            world(level, hidden: hidden) { browser in
                #expect(browser.agentRows().count == rows, "\(level) hidden \(hidden): rows")
                #expect(browser.wordsLead("find cheap flights to tokyo") == lead, "\(level) hidden \(hidden): lead")
                browser.newTab()
                browser.typed = "find cheap flights to tokyo"
                let ai = browser.offers.contains { $0.kind == .ask || $0.kind == .task }
                #expect(ai == (rows > 0), "\(level) hidden \(hidden): offered")
                // ⇥ asks nothing without an Ask row.
                if rows == 0 { #expect(browser.tabAsks == nil, "\(level) hidden \(hidden): tab") }
            }
        }
    }

    @MainActor @Test func entryPointsFollowTheSwitch() {
        for level in AIUse.Level.allCases {
            world(level) { _ in
                let on = level != .off
                #expect(Agents.ready == on, "\(level): agent ready")
                #expect(Agents.connected == on, "\(level): agent connected")
                // ⌘J, Ask About This, Ask About Tab and Image, selection commands.
                #expect(AIUse.offers == on, "\(level): offers")
                // The chat column.
                #expect(AIUse.chats == on, "\(level): chats")
                #expect(!AIUse.connectOffered, "\(level): connected, no Connect row")
            }
        }
    }

    /// D: no plan and no Apple Intelligence: the entry points hide, and
    /// Set up Yab offers the one way in, unless AI is off.
    @MainActor @Test func notConnectedHidesEntryPointsAndOffersConnect() {
        for level in AIUse.Level.allCases {
            world(level, agent: false) { browser in
                #expect(!AIUse.offers, "\(level): offers")
                #expect(AIUse.connectOffered == (level != .off), "\(level): connect row")
                Hints.shared.compose()
                let connect = Hints.shared.queue.contains { $0.id == Hints.connectID }
                // Dismissed before, by a person: it stays away.
                if !(Store.settings.stringArray(forKey: "hints.dismissed") ?? []).contains(Hints.connectID) {
                    #expect(connect == (level != .off), "\(level): connect in the queue")
                }
                #expect(browser.agentRows().isEmpty)
            }
        }
    }

    @MainActor @Test func backgroundWorkStandsDownUnlessOn() {
        for level in AIUse.Level.allCases {
            world(level) { browser in
                browser.prefs.tidyTitles = true
                #expect(ShortNames.enabled == (level == .on), "\(level): tidy titles")
                #expect(MailSense.ready == false || level == .on, "\(level): mail sorting")
                #expect(AIUse.allows(.background) == (level == .on), "\(level): background")
            }
        }
    }

    // MARK: - ⌘T and ⌘K

    @MainActor @Test func commandsOfferedInTheStateTheyChange() {
        let offered: [AIUse.Level: Set<Command>] = [
            .on: [.aiOff, .aiWhenAsked, .hideAIRows],
            .asked: [.aiOff, .aiOn],
            .off: [.aiOn, .aiWhenAsked],
        ]
        let all: [Command] = [.aiOff, .aiOn, .aiWhenAsked, .hideAIRows, .showAIRows]
        for (level, want) in offered {
            world(level) { browser in
                #expect(Set(all.filter { $0.available(in: browser) }) == want, "\(level)")
            }
        }
        world(.on, hidden: true) { browser in
            #expect(Command.showAIRows.available(in: browser))
            #expect(!Command.hideAIRows.available(in: browser))
        }
    }

    @MainActor @Test func commandsRunAndAreReversible() {
        world(.on) { browser in
            Command.aiOff.run(in: browser)
            #expect(browser.prefs.aiUse == .off && AIUse.level() == .off)
            Command.aiWhenAsked.run(in: browser)
            #expect(AIUse.level() == .asked)
            Command.aiOn.run(in: browser)
            #expect(AIUse.level() == .on)
            Command.hideAIRows.run(in: browser)
            #expect(AIUse.rowsHidden() && browser.agentRows().isEmpty)
            Command.showAIRows.run(in: browser)
            #expect(!AIUse.rowsHidden() && browser.agentRows().count == 2)
        }
    }

    /// "turn off ai" names the command outright: it leads, over history
    /// and the engine's suggestions; Hide AI Rows is listed with it.
    @MainActor @Test func typingTurnOffAIFindsIt() {
        world(.on) { browser in
            browser.newTab()
            browser.typed = "turn off ai"
            let kinds = browser.offers.map(\.kind)
            #expect(kinds.first == .command(.aiOff))
            #expect(kinds.contains(.command(.hideAIRows)))
            browser.typed = "ai only when asked"
            #expect(browser.offers.first?.kind == .command(.aiWhenAsked))
        }
        world(.off) { browser in
            browser.newTab()
            browser.typed = "turn on ai"
            #expect(browser.offers.first?.kind == .command(.aiOn))
            #expect(!browser.offers.contains { $0.kind == .command(.aiOff) })
        }
    }

    @MainActor @Test func askRowActionsHideRowsAndTurnOffAI() {
        world(.on) { browser in
            let ask = Suggestion(key: "Ask Claude", title: "answers here", url: nil, kind: .ask)
            let ids = browser.fallbackActions(ask).map(\.id)
            #expect(ids.contains("fallbackRemove"))
            #expect(ids.suffix(3) == ["fallbackHideAI", "fallbackAIOff", "fallbackSettings"])
            let task = Suggestion(key: "Task for Claude", title: "in its own tabs", url: nil, kind: .task)
            #expect(browser.fallbackActions(task).map(\.id).contains("fallbackHideAI"))
            let search = Suggestion(key: "x", title: "Google", url: nil, kind: .search)
            #expect(!browser.fallbackActions(search).map(\.id).contains("fallbackHideAI"))
            browser.fallbackActions(ask).first { $0.id == "fallbackHideAI" }?.run()
            #expect(browser.prefs.aiRowsHidden && AIUse.level() == .on)
        }
    }
}
