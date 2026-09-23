// Exercise the real launcher parser without opening a window or browser.
#define main AxisBridgeAppMainForTest
#import "../packaging/macos/AxisBridgeLauncher.m"
#undef main

@interface ParserTestDelegate : AppDelegate
@end
@implementation ParserTestDelegate
- (void)portalReadyIfPossible {}
@end

int main(void) {
    @autoreleasepool {
        for (NSString *key in @[@"     ", @" example key ", @"ordinary-key"]) {
            for (NSString *ending in @[@"\n", @"\r\n"]) {
                ParserTestDelegate *delegate = [[ParserTestDelegate alloc] init];
                delegate.outputBuffer = [NSMutableString string];
                NSString *lines = [NSString stringWithFormat:@"Portal: http://127.0.0.1:8080%@Access key: %@%@", ending, key, ending];
                // A pipe read may split either the prefix or the whitespace key.
                for (NSUInteger i = 0; i < lines.length; i++) {
                    [delegate consumeOutput:[lines substringWithRange:NSMakeRange(i, 1)]];
                }
                NSCAssert([delegate.accessKey isEqualToString:key], @"The access key changed");
                NSCAssert([delegate.portalURL.absoluteString isEqualToString:@"http://127.0.0.1:8080"], @"Portal URL parsing failed");
                NSCAssert(delegate.outputBuffer.length == 0, @"Unconsumed output");
            }
        }
        puts("Mac launcher: all six key/newline cases passed, including fragmented space-only keys");
    }
    return 0;
}
