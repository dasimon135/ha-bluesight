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

## Request text

> Hi — I'm the author of this topic. Could the opening post be made a wiki, or
> the edit window reopened for it?
>
> It's a custom integration announcement and it has gone stale: the post lists
> an ESPHome component under "Roadmap — next up" that has since shipped, and
> its install section describes two manual steps for the Lovelace card that a
> later release removed. I corrected the second point in a reply, but a reader
> arriving at the topic reads the first post, so the wrong instructions are
> what they act on.
>
> The project ships fairly often, so a wiki would let me keep the first post
> accurate without asking you again after every release. Happy to do it any
> other way you prefer.
>
> Thanks!

## If the answer is no

Keep the *Update reply* in `post-community.md` current instead, and post a new
one on each release that changes what the opening post claims. Less effective,
but it is the honest fallback: the opening post then has to be read as a
snapshot of v0.3.1, and the replies as the real state.
