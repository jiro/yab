import AppKit
import WebKit
import YabStore

/// The existing Apple-model choice also controls the system's selection tools.
@MainActor
final class AppleWritingTools {
    static let shared = AppleWritingTools()
    private let previous = NSMapTable<NSTextView, NSNumber>.weakToStrongObjects()
    private var watchers: [NSObjectProtocol] = []

    nonisolated static func behavior(enabled: Bool) -> NSWritingToolsBehavior {
        enabled ? .default : .none
    }

    static func configure(_ configuration: WKWebViewConfiguration) {
        configuration.writingToolsBehavior = behavior(enabled: AppleModelAccess.usable())
    }

    private init() {
        let center = NotificationCenter.default
        for name in [NSText.didBeginEditingNotification, NSControl.textDidBeginEditingNotification] {
            watchers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] note in
                MainActor.assumeIsolated {
                    let text = (note.object as? NSTextView) ?? ((note.object as? NSTextField)?.currentEditor() as? NSTextView)
                    if let text { self?.apply(to: text) }
                }
            })
        }
        watchers.append(center.addObserver(forName: NSWindow.didBecomeKeyNotification, object: nil, queue: .main) { [weak self] note in
            MainActor.assumeIsolated {
                if let window = note.object as? NSWindow, let text = window.firstResponder as? NSTextView {
                    self?.apply(to: text)
                }
            }
        })
        watchers.append(center.addObserver(forName: AppleModelAccess.changed, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        })
        StallProbe.mark("AppleWritingTools.observers")
    }

    deinit {
        for watcher in watchers { NotificationCenter.default.removeObserver(watcher) }
    }

    func apply(to text: NSTextView) {
        let behavior: NSWritingToolsBehavior
        if AppleModelAccess.usable() {
            guard let saved = previous.object(forKey: text),
                  let restored = NSWritingToolsBehavior(rawValue: saved.intValue) else { return }
            previous.removeObject(forKey: text)
            behavior = restored
        } else {
            if previous.object(forKey: text) == nil {
                previous.setObject(NSNumber(value: text.writingToolsBehavior.rawValue), forKey: text)
            }
            behavior = .none
        }
        if text.writingToolsBehavior != behavior { text.writingToolsBehavior = behavior }
    }

    private func refresh() {
        for text in previous.keyEnumerator().allObjects.compactMap({ $0 as? NSTextView }) { apply(to: text) }
        for window in NSApplication.shared.windows {
            if let text = window.firstResponder as? NSTextView { apply(to: text) }
            if let content = window.contentView { visit(content) }
        }
    }

    private func visit(_ view: NSView) {
        // WebKit copies this policy at creation. Changing configuration on an
        // existing view cannot update it, even after a navigation or reload.
        guard !(view is WKWebView) else { return }
        if let text = view as? NSTextView { apply(to: text) }
        for child in view.subviews { visit(child) }
    }
}
