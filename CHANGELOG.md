# Changelog

Every release gets a short, plain-language entry here. The `release` workflow
publishes the matching section as the GitHub release notes, and the app shows
those notes to users when it tells them an update is available. Write for
students, not developers: say what changed and why it matters.

## 0.1.3 - 2026-09-17

**Handouts linked inside a page are now downloaded too.** Some course pages
(for example "Lecture 3 Content and Materials") keep a handout or reading as a
link inside the page itself rather than as an attached file. The app now
follows those links and saves the file next to the page. Links written as full
web addresses are recognised now as well, so they no longer come out as mere
shortcuts. As before, if the browser cookies have expired these stay as
clickable shortcuts until you sign in again.

**Sessions that only had text now show up.** Some practicum sessions describe
their material in the session description without attaching any file, so their
folders used to come out empty. Each description is now saved as a readable
Markdown note, so nothing is missed.

## 0.1.2 - 2026-09-16

**The app now tells you when a new version is available.** When it starts,
and once a day after that, it quietly checks for an update. If there is one
you get a notification with a short summary of what changed, plus a new menu
item that shows the full notes and a Download button.

This is the first release that can announce itself, so to start receiving
these notices you need to install this version once by hand.

## 0.1.1 - 2026-09-16

**Signing in is now the first step, so setup is much clearer.** The account
section sits at the top of the window, and the main button walks you through
it: it reads "Sign In to Continue" until you are signed in, then changes to
"Start Syncing". Before, that button just refused with a dead-end "sign in
first" message.

**The app now works without Google Chrome.** If Chrome is not installed, it
explains what changes and offers to continue using your normal browser
instead of failing. Without Chrome, the course reader (for example the
Statistics 1a reader) is saved as a clickable link rather than downloaded.

## 0.1.0 - 2026-09-13

First release: a native macOS menu bar app that keeps a copy of all your
Brightspace course material on your Mac and notifies you when something new
appears.
