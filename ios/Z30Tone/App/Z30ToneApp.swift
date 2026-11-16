import SwiftUI
import Z30ToneCore

@main
struct Z30ToneApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                WriterScreen()
            }
            .environmentObject(model)
            .tint(.accentColor)
        }
    }
}
