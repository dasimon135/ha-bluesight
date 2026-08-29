# Asking the HA community moderators to wiki the announcement post

The opening post of the English thread cannot be edited any more. Discourse
closes editing after `post_edit_time_limit`, which is 24 hours at the lower
trust levels, and the thread went up on 2026-08-24.

That matters more each release. A reader lands on the opening post, so anything
it gets wrong is what people act on — and it currently announces a feature as
"next up" that shipped a month ago, and walks through two install steps that no
longer exist. Corrections posted as replies are read by a small fraction of the
people who read the first post; that has already happened once here.

A **wiki post** stays editable indefinitely by its author. Asking for one fixes
this release and every one after it, which is why it is worth a moderator's
time rather than a one-off edit request.

## How

On the thread: **flag the opening post → Something Else**, and paste the text
below. (A flag routes to the moderator queue; it is the normal channel for this
kind of request and not a report against anyone.)

**That field caps at 500 characters**, which is the binding constraint here —
the request has to make its case in a paragraph, not a page. The text below is
461 characters. Do not pad it: newlines may be sent as CRLF, so anything within
a few characters of the cap risks being rejected on paste.

## Request text (461 chars)

> Hi — I'm the author of this topic. Could the first post be made a wiki, or
> its edit window reopened?
>
> The announcement has gone stale: it lists a feature as "next up" that has
> since shipped, and its install section describes two manual steps a later
> release removed. I corrected that in a reply, but people read the first post,
> so the stale instructions are what they follow.
>
> The project ships often, so a wiki would save asking you again each release.
> Thanks!

### Shorter fallback (403 chars)

If the field rejects the above for any reason, this drops the roadmap point and
keeps the one that actually misleads people:

> Hi — I'm the author of this topic. Could the first post be made a wiki, or
> its edit window reopened?
>
> The announcement has gone stale: its install section describes two manual
> steps a later release removed, so readers follow instructions that no longer
> apply. I corrected it in a reply, but people read the first post.
>
> A wiki would let me keep it accurate without asking you again each release.
> Thanks!

## If the answer is no

Keep the *Update reply* in `post-community.md` current instead, and post a new
one on each release that changes what the opening post claims. Less effective,
but it is the honest fallback: the opening post then has to be read as a
snapshot of v0.3.1, and the replies as the real state.
