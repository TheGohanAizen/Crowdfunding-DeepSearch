# V5 URL identity migration review

**Status:** Design-only, offline. Do not run an automatic migration against user data.

The offline opportunity store currently identifies duplicates by a normalized
HTTPS URL per verified owner. Normalization removes fragments, lowercases the
host, removes the default HTTPS port, and uppercases percent-escape hex digits.
It **preserves path trailing slashes, query parameter order, and encoded reserved
characters**.

## Why this needs review

Earlier V5 prototypes stripped trailing path slashes. Thus two distinct
sources, such as `https://example.org/apply` and
`https://example.org/apply/`, could previously have been merged into one
record. Once merged, a missing source link cannot be reconstructed from the
remaining record. Never invent or silently split such records.

## Safe migration prerequisites

1. Verify the offline test suite and make a read-only backup of any database
   before a schema or URL-normalization change.
2. Record the original URL, normalized URL, owner ID, record ID, and metadata
   for each existing record, without logging or exporting sensitive data.
3. Run the proposed normalization on a **copy** and report collisions grouped
   by owner. Do not assume the absence of collisions.
4. For a collision, retain all original records and notes until a human decides
   whether they refer to the same opportunity. Never overwrite review status
   or notes automatically.
5. Require an explicit migration plan with rollback and post-migration
   owner-isolation checks before writing to a real store.
6. Treat browser-local V1–V4 records as a separate source of truth; never
   silently import them into V5 or imply they are already synchronized.

## Launch boundary

This document does not add V5 HTTP routes, enable authentication on the live
site, deploy to Render, contact organizations, or authorize paid discovery.
