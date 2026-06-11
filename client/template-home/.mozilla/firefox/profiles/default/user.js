// GraceLab guest Firefox profile — applied on every session reset.
// user.js is read by Firefox on every launch and overrides prefs.js.

// Homepage
user_pref("browser.startup.homepage", "https://guestdesk.info");
user_pref("browser.startup.page", 1);

// Suppress all first-run / onboarding flows
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.shell.didSkipDefaultBrowserCheckOnFirstRun", true);
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
user_pref("browser.uitour.enabled", false);
user_pref("browser.rights.3.shown", true);
user_pref("datareporting.policy.firstRunURL", "");
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);

// Disable default browser prompt
user_pref("browser.shell.checkDefaultBrowser", false);

// Disable session restore prompt after crash
user_pref("browser.sessionstore.resume_from_crash", false);

// Disable "Firefox is not your default browser" bar
user_pref("browser.defaultbrowser.notificationbar", false);
