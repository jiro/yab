import Darwin
import Foundation

// Temporary, for the full-suite stall experiment on a fork's CI
// (branch chore/full-suite-ci-experiment). Not for main.
//
// YAB_FIX_COMPOSER=mail  ChatComposer writes mailShown only when the key
//                        differs (condition M).
// YAB_CI_MARKS=<path>    one line per mark: wall-clock seconds, the mark,
//                        whether it ran on the main thread, the thread's
//                        Mach port (to pair begin and end) and the process
//                        (the XCTest half writes marks too). Written
//                        with write(2), unbuffered, so a line is on disk
//                        even when the process stalls or is killed.
public enum StallProbe {
    public static let mail: Bool = {
        let on = (ProcessInfo.processInfo.environment["YAB_FIX_COMPOSER"] ?? "").split(separator: ",").contains("mail")
        // In the marks, so a trial shows which way the switch was read.
        mark("StallProbe.mail=\(on)")
        return on
    }()

    private static let marks: Int32 = {
        guard let path = ProcessInfo.processInfo.environment["YAB_CI_MARKS"], !path.isEmpty else { return -1 }
        return open(path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
    }()

    public static func mark(_ what: String) {
        guard marks >= 0 else { return }
        var now = timeval()
        gettimeofday(&now, nil)
        let seconds = Double(now.tv_sec) + Double(now.tv_usec) / 1_000_000
        let thread = pthread_mach_thread_np(pthread_self())
        let line = "\(seconds)\t\(what)\t\(Thread.isMainThread ? "main" : "other")\t\(thread)\t\(getpid())\n"
        line.withCString { _ = write(marks, $0, strlen($0)) }
    }
}
