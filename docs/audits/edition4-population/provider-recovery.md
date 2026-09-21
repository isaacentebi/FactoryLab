# A lost provider response must not end every rehearsal

PR117's population stopped after 26 ticks because external admission made one
dispatched `OpenRouterError` permanently terminal. The runtime had already recorded
the failed invocation and could continue. Increasing capital would not change that
admission decision. The underlying transport cause was not established by the diary.

The rehearsal wrapper now keeps the failed call's entire quote in `uncertain_micro`
and admits later work only against the remaining cap. It rethrows the original
exception: no fabricated response, automatic retry, refund or replacement completion
is introduced. A later successful, authoritatively billed call resets the consecutive
failure count; it does not release any previous uncertainty. Core balance-based
reconciliation remains separate from this conservative external liability.

Three consecutive provider exceptions stop admission, including provably unbilled
exceptions. Every attempted call counts toward the existing call limit. A failed
dispatch can also exhaust the cap immediately. Reported overruns, table-derived
costs and successful responses without authoritative billing still stop admission
immediately. Reports expose the current failure streak and its limit. This changes
the experiment supervisor only, not population objectives, norms, rewards or kernel
money rules. No running world is modified.

Recovery is enabled explicitly by the population runner. Shared admission users
such as short discovery and investigation probes retain their prior immediate-stop
behavior; the report records which policy was used.

The offline acceptance injects a lost response on call two into the actual rehearsal
runner with its real meter and a fake exchange. It requires later invocation under
a different decision handle, five delivered ticks, retained external uncertainty,
correct known-call accounting, conserved wallet, terminal seal and no orders.
Additional focused checks cover no hidden retry, successful recovery without
releasing liability, consecutive failure shutdown, and cap exhaustion by one error.

This proves recovery from an isolated provider exception, not that the provider is
reliable, that a quoted ceiling bounds every possible upstream charge, or that the
population learns. Billing uncertainty must remain visible in the coverage report.
No paid provider request, live rerun or deployment was used to validate this repair.
