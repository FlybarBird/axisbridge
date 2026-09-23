#import <Cocoa/Cocoa.h>

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSTextField *detailLabel;
@property(nonatomic, strong) NSButton *openButton;
@property(nonatomic, strong) NSTask *serverTask;
@property(nonatomic, strong) NSPipe *outputPipe;
@property(nonatomic, strong) NSMutableString *outputBuffer;
@property(nonatomic, strong) NSURL *portalURL;
@property(nonatomic, copy) NSString *accessKey;
@property(nonatomic) BOOL openedPortal;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    self.outputBuffer = [NSMutableString string];
    [self makeMenu];
    [self makeWindow];
    [NSApp activateIgnoringOtherApps:YES];
    [self startServer];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    self.outputPipe.fileHandleForReading.readabilityHandler = nil;
    if (self.serverTask.running) {
        [self.serverTask terminate];
        [self.serverTask waitUntilExit];
    }
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return NO; }

- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)hasVisibleWindows {
    if (!hasVisibleWindows) [self.window makeKeyAndOrderFront:nil];
    return YES;
}

- (void)makeMenu {
    NSMenu *mainMenu = [[NSMenu alloc] init];
    NSMenuItem *appItem = [[NSMenuItem alloc] init];
    NSMenu *appMenu = [[NSMenu alloc] init];
    [appMenu addItemWithTitle:@"Open AxisBridge Portal" action:@selector(openPortal:) keyEquivalent:@"o"];
    [appMenu addItem:[NSMenuItem separatorItem]];
    [appMenu addItemWithTitle:@"Quit AxisBridge" action:@selector(terminate:) keyEquivalent:@"q"];
    appItem.submenu = appMenu;
    [mainMenu addItem:appItem];
    NSApp.mainMenu = mainMenu;
}

- (void)makeWindow {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 520, 265)
                                              styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                                                backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"AxisBridge";
    self.window.releasedWhenClosed = NO;
    [self.window center];
    NSView *content = [[NSView alloc] init];
    self.window.contentView = content;

    NSTextField *title = [NSTextField labelWithString:@"AxisBridge"];
    title.font = [NSFont systemFontOfSize:30 weight:NSFontWeightBold];
    NSTextField *subtitle = [NSTextField labelWithString:@"PSN position into grandMA2 playback"];
    subtitle.font = [NSFont systemFontOfSize:14];
    subtitle.textColor = NSColor.secondaryLabelColor;
    self.statusLabel = [NSTextField labelWithString:@"Starting AxisBridge…"];
    self.statusLabel.font = [NSFont systemFontOfSize:16 weight:NSFontWeightSemibold];
    self.detailLabel = [NSTextField wrappingLabelWithString:@"The local portal will open automatically when the service is ready."];
    self.detailLabel.font = [NSFont systemFontOfSize:13];
    self.detailLabel.textColor = NSColor.secondaryLabelColor;
    self.detailLabel.maximumNumberOfLines = 2;
    self.openButton = [NSButton buttonWithTitle:@"Open Portal" target:self action:@selector(openPortal:)];
    self.openButton.bezelStyle = NSBezelStyleRounded;
    self.openButton.keyEquivalent = @"\r";
    self.openButton.enabled = NO;
    NSButton *quitButton = [NSButton buttonWithTitle:@"Quit AxisBridge" target:NSApp action:@selector(terminate:)];
    quitButton.bezelStyle = NSBezelStyleRounded;

    NSArray<NSView *> *views = @[title, subtitle, self.statusLabel, self.detailLabel, self.openButton, quitButton];
    for (NSView *view in views) {
        view.translatesAutoresizingMaskIntoConstraints = NO;
        [content addSubview:view];
    }
    [NSLayoutConstraint activateConstraints:@[
        [title.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:30],
        [title.topAnchor constraintEqualToAnchor:content.topAnchor constant:28],
        [subtitle.leadingAnchor constraintEqualToAnchor:title.leadingAnchor],
        [subtitle.topAnchor constraintEqualToAnchor:title.bottomAnchor constant:4],
        [self.statusLabel.leadingAnchor constraintEqualToAnchor:title.leadingAnchor],
        [self.statusLabel.topAnchor constraintEqualToAnchor:subtitle.bottomAnchor constant:35],
        [self.statusLabel.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-30],
        [self.detailLabel.leadingAnchor constraintEqualToAnchor:title.leadingAnchor],
        [self.detailLabel.topAnchor constraintEqualToAnchor:self.statusLabel.bottomAnchor constant:8],
        [self.detailLabel.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-30],
        [self.openButton.leadingAnchor constraintEqualToAnchor:title.leadingAnchor],
        [self.openButton.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-25],
        [self.openButton.widthAnchor constraintEqualToConstant:130],
        [quitButton.leadingAnchor constraintEqualToAnchor:self.openButton.trailingAnchor constant:10],
        [quitButton.centerYAnchor constraintEqualToAnchor:self.openButton.centerYAnchor],
        [quitButton.widthAnchor constraintEqualToConstant:130]
    ]];
    [self.window makeKeyAndOrderFront:nil];
}

- (void)startServer {
    NSURL *executable = [NSBundle.mainBundle URLForResource:@"axisbridge" withExtension:nil];
    if (!executable) { [self showFailure:@"The bundled AxisBridge service is missing."]; return; }
    NSTask *task = [[NSTask alloc] init];
    NSPipe *pipe = [NSPipe pipe];
    task.executableURL = executable;
    task.arguments = @[@"--no-browser", @"--host", @"127.0.0.1", @"--port", @"8080"];
    task.standardOutput = pipe;
    task.standardError = pipe;
    self.serverTask = task;
    self.outputPipe = pipe;
    __weak typeof(self) weakSelf = self;
    pipe.fileHandleForReading.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *data = handle.availableData;
        if (!data.length) return;
        NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        if (!text) return;
        dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf consumeOutput:text]; });
    };
    task.terminationHandler = ^(NSTask *finished) {
        dispatch_async(dispatch_get_main_queue(), ^{
            AppDelegate *strongSelf = weakSelf;
            if (!strongSelf) return;
            strongSelf.outputPipe.fileHandleForReading.readabilityHandler = nil;
            if (!strongSelf.openedPortal) {
                [strongSelf showFailure:[NSString stringWithFormat:@"AxisBridge stopped before the portal became ready (exit %d).", finished.terminationStatus]];
            } else {
                strongSelf.statusLabel.stringValue = @"AxisBridge stopped";
                strongSelf.detailLabel.stringValue = @"Quit and reopen the application to start it again.";
                strongSelf.openButton.enabled = NO;
            }
        });
    };
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self showFailure:[NSString stringWithFormat:@"AxisBridge could not start: %@", error.localizedDescription]];
    }
}

- (void)consumeOutput:(NSString *)text {
    [self.outputBuffer appendString:text];
    NSRange newline;
    while ((newline = [self.outputBuffer rangeOfString:@"\n"]).location != NSNotFound) {
        NSString *line = [[self.outputBuffer substringToIndex:newline.location]
            stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        [self.outputBuffer deleteCharactersInRange:NSMakeRange(0, NSMaxRange(newline))];
        if ([line hasPrefix:@"Portal: "]) self.portalURL = [NSURL URLWithString:[line substringFromIndex:@"Portal: ".length]];
        else if ([line hasPrefix:@"Access key: "]) self.accessKey = [line substringFromIndex:@"Access key: ".length];
    }
    [self portalReadyIfPossible];
}

- (void)portalReadyIfPossible {
    if (self.openedPortal || !self.portalURL || !self.accessKey) return;
    self.openedPortal = YES;
    self.statusLabel.stringValue = @"● AxisBridge is running";
    self.statusLabel.textColor = NSColor.systemGreenColor;
    self.detailLabel.stringValue = [NSString stringWithFormat:@"Portal: %@ · Output starts held for safety.", self.portalURL.absoluteString];
    self.openButton.enabled = YES;
    [self openPortal:nil];
}

- (void)openPortal:(id)sender {
    if (!self.portalURL || !self.accessKey) return;
    NSURLComponents *components = [NSURLComponents componentsWithURL:self.portalURL resolvingAgainstBaseURL:NO];
    components.fragment = [NSString stringWithFormat:@"key=%@", self.accessKey];
    if (components.URL) [NSWorkspace.sharedWorkspace openURL:components.URL];
}

- (void)showFailure:(NSString *)message {
    self.statusLabel.stringValue = @"AxisBridge could not start";
    self.statusLabel.textColor = NSColor.systemRedColor;
    self.detailLabel.stringValue = message;
    self.openButton.enabled = NO;
    [NSApp requestUserAttention:NSCriticalRequest];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        AppDelegate *delegate = [[AppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
