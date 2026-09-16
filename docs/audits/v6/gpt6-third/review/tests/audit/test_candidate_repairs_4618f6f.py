"""Review candidates built in memory, never applied to the production tree.

These are small repairs, not the typed-treasury/finality/feedback redesign. Imported
SDK-dependent functions are tested in isolation as in test_cold_4618f6f.py.
"""
from __future__ import annotations
import ast
import difflib
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('cold_audit_helpers', HERE/'test_cold_4618f6f.py')
cold = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cold
_spec.loader.exec_module(cold)
prohibit_network_and_key_reads = cold.prohibit_network_and_key_reads
ROOT = cold.ROOT


def candidate_sources():
    out = {}
    def source(path):
        return out.get(path, (ROOT/path).read_text())
    def edit(path, before, after, *, count=1):
        s = source(path)
        assert s.count(before) == count, (path, s.count(before), before[:70])
        out[path] = s.replace(before, after)
    def function(path, name, replacement, cls=None):
        s = source(path)
        tree = ast.parse(s)
        nodes = tree.body if cls is None else next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
        node = next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)
        lines = s.splitlines(keepends=True)
        out[path] = ''.join(lines[:node.lineno-1])+replacement.rstrip()+'\n'+''.join(lines[node.end_lineno:])

    p='factorylab/cortex/request.py'
    edit(p, 'Money = int\n', '''Money = int


def public_return(outputs: Any) -> dict[str, Any]:
    """Project a return across a contract boundary, excluding continuity internals."""
    if not isinstance(outputs, dict):
        return {"invalid_return": True}
    return {k: v for k, v in outputs.items()
            if k not in {"working_state", "ack_through", "raw"}}
''')
    edit(p, 'inputs = {**self.inputs, "world": moving} if stable else self.inputs',
         'inputs = ({**self.inputs, "world": moving}\n                  if isinstance(self.inputs.get("world"), dict) else self.inputs)')
    for p in ('factorylab/runtime/loop.py','factorylab/runtime/compute.py'):
        text = 'from factorylab.cortex.request import '
        line = next(l for l in source(p).splitlines() if l.startswith(text))
        edit(p, line, line+', public_return')
        n=source(p).count('"outputs": ret.outputs')
        edit(p, '"outputs": ret.outputs', '"outputs": public_return(ret.outputs)', count=n)
    p='factorylab/runtime/loop.py'
    edit(p, '''            if self._event_subject(ev) is not None:
                self.stats.noops += 1
                self.consequences.finish(handle, 0)
                self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                                  status=SettleStatus.INAPPLICABLE,
                                  definition_version=DEF_VERDICT, sampling_ref=None)
            else:
                self._producer_step(ev, handle, sample, deadline)''', '''            # A router abstention is not an authored producer return.
            self.stats.noops += 1
            self.consequences.finish(handle, 0)
            self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                              status=SettleStatus.INAPPLICABLE,
                              definition_version=DEF_VERDICT, sampling_ref=None)''')
    p='factorylab/runtime/wake.py'
    edit(p, 'from factorylab.kernel.ledger import Ledger, LedgerIntegrityError, canonical',
         'from factorylab.cortex.request import public_return\nfrom factorylab.kernel.ledger import Ledger, LedgerIntegrityError, canonical')
    function(p, '_outputs', '''def _outputs(value):
    """Publish the contract result, never private state or an unparseable raw prefix."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"outputs_unavailable": "unparseable_or_truncated"}
    return public_return(value)
''')
    p='factorylab/cortex/schematics.py'
    edit(p, 'for d in self.queue.outstanding(seat)',
         'for d in self.queue.outstanding()\n            if d.actor == seat or self.handle_to_assembly.get(d.handle) == seat')
    p='factorylab/runtime/subscriptions.py'
    edit(p, '    return fact, seen\n', '    return fact, seen if now is not None else dict(last or {})\n')

    p='factorylab/kernel/artifacts.py'
    edit(p, '        self.ledger.append({"kind": "artifact.put",',
         '        self._write(sha, data)  # Durable bytes before any authenticated reference.\n        self.ledger.append({"kind": "artifact.put",')
    edit(p, '''        self._write(sha, data)
        return sha''', '''        record = self.index[sha]
        record["readers"] = sorted(set(record.get("readers", [record["owner"]])) | {owner})
        record["public"] = bool(record.get("public") or public)
        return sha''')
    edit(p, '''            return self._memory[sha]''', '''            data = self._memory[sha]
            if hashlib.sha256(data).hexdigest() != sha:
                raise ArtifactError("artifact bytes do not match their hash")
            return data''')
    edit(p, 'if owner is None or record["owner"] == owner]',
         'if owner is None or owner in record.get("readers", [record["owner"]])]')
    edit(p, '''        if record is None or reader is None:
            return True
        if record["owner"] == reader or record.get("public"):''', '''        if record is None:
            return False  # Unindexed durable bytes confer no read authority.
        if reader is None:
            return True  # Kernel-only inspection retains its existing contract.
        if reader in record.get("readers", [record["owner"]]) or record.get("public"):''')
    edit(p, '''            self._memory.setdefault(sha, data)
            return''', '''            existing = self._memory.get(sha)
            if existing is not None and existing != data:
                raise ArtifactError("artifact bytes do not match their hash")
            self._memory.setdefault(sha, data)
            return''')
    edit(p, '''        if path.exists():
            return  # content-addressed: the same bytes are already there''', '''        if path.exists():
            self.get(sha)  # Verify an existing file instead of blessing corruption.
            return''')
    edit(p, '            os.replace(temporary, path)', '''            os.replace(temporary, path)
            directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)''')

    p='factorylab/runtime/continuity.py'
    edit(p, 'import json\n', 'import hashlib\nimport json\n')
    edit(p, '''        self.heads[seat] = {
            "sha": sha,''', '''        successor = {
            "sha": sha,''')
    edit(p, '''            "rent_ns": previous.get("rent_ns", now) if previous else now,''', '''            "rent_ns": now,
            "rent_byte_ns": (previous.get("rent_byte_ns", 0) + previous["bytes"] *
                             max(0, now - previous.get("rent_ns", now))) if previous else 0,''')
    edit(p, '''        return self.heads[seat]

    def render''', '''        self.heads[seat] = successor
        return successor

    def render''')
    edit(p, '''        except Exception:  # noqa: BLE001 - a head whose bytes are gone shows as absent
            return None''', '''        except Exception as exc:
            raise RuntimeError("working state is present but unavailable") from exc''')
    edit(p, '''        while len(self.said) > MAX_SAID:
            self.said.pop(next(iter(self.said)))''', '''        # Do not evict addressability by unrelated traffic. A future archive-backed
        # GC must prove that no delayed consequence can reference a removed handle.''')
    edit(p, '''        observed = self.clock() if observed_at_ns is None else observed_at_ns''', '''        fact = (hashlib.sha256(canonical({"handle": handle, "outcome": outcome,
                    "delta_micro": int(delta_micro), "evidence": evidence})).hexdigest()
                if evidence is not None else None)
        if fact is not None:
            for existing in self.items.get(seat, ()):
                if existing.get("fact") == fact:
                    return existing
        observed = self.clock() if observed_at_ns is None else observed_at_ns''')
    edit(p, '''        self.seq += 1
        record = {"seq": self.seq, "handle": handle, "sha": sha, "observed_at_ns": observed}
        self.items.setdefault(seat, []).append(record)''', '''        next_seq = self.seq + 1
        record = {"seq": next_seq, "handle": handle, "sha": sha,
                  "observed_at_ns": observed, "fact": fact}''')
    edit(p, '''"sha": sha, "item": self.seq, "delta_micro": int(delta_micro),''',
         '''"sha": sha, "item": next_seq, "delta_micro": int(delta_micro),''')
    edit(p, '''                            "evidence": evidence, "ts": observed})
        return record''', '''                            "evidence": evidence, "ts": observed})
        self.seq = next_seq
        self.items.setdefault(seat, []).append(record)
        return record''')
    edit(p, '''        except Exception:  # noqa: BLE001 - a body whose bytes are gone is reported absent
            return None''', '''        except Exception as exc:
            raise RuntimeError("addressed outcome is unavailable") from exc''')
    function(p, 'unread', '''    def unread(self, seat: str) -> dict[str, Any]:
        """Deliver oldest-first, with unambiguous item addresses, until acknowledged."""
        cursor = self.cursors.get(seat, 0)
        rows = [r for r in self.items.get(seat, ()) if r["seq"] > cursor]
        return {"count": len(rows), "items": [
            {**self.body(r["sha"]), "outcome_id": f"outcome:{r['seq']}"}
            for r in rows[:INLINE_OUTCOMES]]}
''', 'OutcomeInbox')
    edit(p, 'if record["handle"] == handle:',
         'if record["handle"] == handle or handle == f"outcome:{record[\'seq\']}":', count=2)
    edit(p, '''                        return {**body, "sha": record["sha"],
                                "read": record["seq"] <= self.cursors.get(seat, 0)}''', '''                        return {**body, "sha": record["sha"],
                                "outcome_id": f"outcome:{record['seq']}",
                                "related_outcomes": [f"outcome:{r['seq']}"
                                    for r in self.items.get(seat, ())
                                    if r["handle"] == record["handle"]],
                                "read": record["seq"] <= self.cursors.get(seat, 0)}''')
    edit(p, '                self.cursors[seat] = cursor\n', '')
    edit(p, '''                                    "handle": handle, "cursor": cursor, "ts": self.clock()})
                return cursor''', '''                                    "handle": handle, "cursor": cursor, "ts": self.clock()})
                self.cursors[seat] = cursor
                return cursor''')
    edit(p, '        head["rent_ns"], head["rent_carry"] = now_ns, carry',
         '        head["rent_ns"], head["rent_carry"] = now_ns, carry\n        head["rent_byte_ns"] = 0')
    p='factorylab/runtime/notes.py'
    edit(p, '''    owed = entry["bytes"] * max(0, now_ns - since) * numerator + entry.get("rent_carry", 0)''',
         '''    byte_ns = entry.get("rent_byte_ns", 0) + entry["bytes"] * max(0, now_ns - since)
    owed = byte_ns * numerator + entry.get("rent_carry", 0)''')

    p='factorylab/settlement/settle.py'
    start=source(p).index('        # A fidelity objection is scored like the verdict')
    end=source(p).index('        if key not in self.__recorded:',start)
    out[p]=source(p)[:start]+'''        # The challenged proxy cannot certify or refute its own fidelity.
        # Keep the claim in the returned evidence; await independent adjudication.
        objection = self.__objections.pop(judge_handle, None) if judge_handle else None
        objection_score = objection_baseline = None
'''+source(p)[end:]
    p='factorylab/settlement/fidelity.py'
    edit(p, '''            "evidence, and your uncertainty that the objection is right. It is scored like your "
            "verdict and it is open to challenge; it is not a verdict on its own."''', '''            "evidence, and your uncertainty that the objection is right. It remains an "
            "open, contestable claim pending independent evidence; the challenged proxy "
            "does not score its own fidelity."''')

    p='factorylab/kernel/budget.py'
    # Automatic late income uses the existing retirement rule: proceeds stay in commons.
    for method, result in [('earn','None'), ('credit','0')]:
        tree=ast.parse(source(p));cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BudgetBook')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method)
        lines=source(p).splitlines(keepends=True);text=''.join(lines[fn.lineno-1:fn.end_lineno])
        target='        require_money(amount, nonnegative=True)\n'
        after=target+f'''        if seat in self.__retired:
            self._log("retired_{method}", assembly_id=seat, amount=amount, reason=reason,
                      unallocated_after=self.unallocated())
            return {result}
'''
        assert text.count(target)==1
        out[p]=''.join(lines[:fn.lineno-1])+text.replace(target,after)+''.join(lines[fn.end_lineno:])

    p='factorylab/runtime/resume.py'
    anchor='    running_facilitator = getattr(rt, "facilitator_url", None)\n'
    # One occurrence belongs to restore_runtime, another to the outer resume procedure.
    old=source(p);pos=old.index(anchor,old.index('def restore_runtime('))+len(anchor)
    out[p]=old[:pos]+'''    # Identity validation precedes every mutation of the destination runtime.
    saved_digest = saved_runtime.get("release_digest")
    if saved_digest is not None and saved_digest != running_digest:
        raise ResumeError("release digest differs from the saved world", code="release_mismatch")
    saved_facilitator = saved_runtime.get("facilitator_url")
    if saved_facilitator is not None and saved_facilitator != running_facilitator:
        raise ResumeError("x402 facilitator differs from the saved world",
                          code="facilitator_mismatch")
'''+old[pos:]
    # Existing later checks are intentionally retained as defense in depth.
    p='factorylab/runtime/venue.py'
    edit(p, '''        self.registration_feedback.append({"kind": kind,
                                           "reason": f"order: {reason}"})''', '''        self.registration_feedback.append({"kind": kind,
                                           "reason": f"order: {reason}"})
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is not None:
            self.outcomes.append(owner, handle=handle,
                outcome={"kind": "order_refused", "status": "rejected", "reason": reason,
                         **extra}, delta_micro=0,
                evidence={"kind": kind, "handle": handle, "ts": self.clock.now_ns})''')
    edit(p, '''        ok = result.get("status") in ("filled", "cancelled", "resting", "ok")''', '''        ok = result.get("status") in (
            ("cancelled", "ok") if kind == "cancelled" else ("filled",))''')
    # This repair guarantees local termination even if wind-down or its journal fails.
    # A durable, independent exit reconciler is an architectural prerequisite, not supplied here.
    start=source(p).index('        report = {"attempted": False, "orders": 0}',source(p).index('    def kill('))
    end=source(p).index('        return report',start)+len('        return report')
    out[p]=source(p)[:start]+'''        if self.termination.final:
            return getattr(self, "wind_down_report", {"attempted": False, "orders": 0,
                                                      "exposure_status": "unknown"})
        report = {"attempted": False, "orders": 0, "exposure_status": "unknown"}
        exchange = getattr(self, "exchange", None)
        try:
            if self.m.kill.wind_down and exchange is not None:
                report = wind_down(exchange, self.ledger, dust_micro=self.m.kill.dust_micro)
            elif self.m.kill.wind_down:
                report["error"] = "world has no exchange"
        except Exception as exc:
            report["error"] = type(exc).__name__
        finally:
            # An acknowledgement is not a reconciled flat account.
            report.setdefault("exposure_status", "unknown")
            self.wind_down_report = report
            try:
                witness.note_wind_down(wind_down=bool(self.m.kill.wind_down),
                                       orders=report.get("orders", 0))
            finally:
                self.termination.kill(reason)
        return report'''+source(p)[end:]
    p='factorylab/world/treasury.py'
    edit(p, '"spool_offset": 0}', '"spool_offset": 0, "receipts": {}}')
    edit(p, 'def earn(self, service: str, micro: int, tx: str, **detail) -> dict:',
         'def earn(self, service: str, micro: int, tx: str, **detail) -> dict | None:')
    edit(p, '        item = {"kind": "income.earned", "service": service, "micro": micro, "tx": tx, **detail}',
         '''        receipt_id = tx.lower() if tx.startswith("0x") else tx
        signature = {"service": service, "micro": micro,
                     **{k: detail.get(k) for k in ("payer", "program", "version")}}
        receipts = self.income.get("receipts", {})
        if receipt_id in receipts:
            if receipts[receipt_id] != signature:
                raise ValueError("settlement reference reused with conflicting payment")
            return None
        item = {**detail, "kind": "income.earned", "service": service, "micro": micro, "tx": tx}''')
    edit(p, '        self.income = {**self.income, "earned_micro": self.income["earned_micro"] + micro}',
         '''        self.income = {**self.income, "earned_micro": self.income["earned_micro"] + micro,
                       "receipts": {**receipts, receipt_id: signature}}''')
    edit(p, '            booked.append(self.earn(', '            item = self.earn(')
    edit(p, '''                version=receipt.get("version"), served_ns=receipt.get("ts"),
            ))''', '''                version=receipt.get("version"), served_ns=receipt.get("ts"),
            )
            if item is not None:
                booked.append(item)''')
    p='factorylab/runtime/seller.py'
    edit(p, "        rt._book_income(item)  # C10: the owning seat's entitlement grows with its income",
         '''        if item is not None:
            rt._book_income(item)  # A repeated receipt never credits money twice.''')
    return out


def unified_patch():
    return ''.join(''.join(difflib.unified_diff((ROOT/p).read_text().splitlines(True),
                       s.splitlines(True), fromfile='a/'+p, tofile='b/'+p, n=3))
                   for p,s in sorted(candidate_sources().items()))


def load_candidate(path):
    name='candidate_'+path.replace('/','_').replace('.','_')
    module=types.ModuleType(name);module.__file__=str(ROOT/path);sys.modules[name]=module
    exec(compile(candidate_sources()[path],str(ROOT/path),'exec'),module.__dict__)
    return module


def test_all_candidate_sources_compile_without_modifying_production():
    assert len(candidate_sources()) >= 12
    for path,source in candidate_sources().items():
        compile(source, str(ROOT/path), 'exec')


def test_candidate_state_and_inbox_fail_closed_and_write_ahead():
    c=load_candidate('factorylab/runtime/continuity.py')
    ledger,clock,store=cold.archive();state=c.WorkingState(store,ledger,lambda:clock.ns)
    original=state.put('alice',{'v':1},handle='d1')['sha'];ledger.fail_kind='state.put'
    with pytest.raises(OSError):state.put('alice',{'v':2},handle='d2')
    assert state.head('alice')['sha']==original
    store._memory.pop(original)
    with pytest.raises(RuntimeError):state.render('alice')
    ledger.fail_kind='outcome.addressed';inbox=c.OutcomeInbox(store,ledger,lambda:clock.ns)
    with pytest.raises(OSError):inbox.append('alice',handle='d1',outcome={'v':1})
    assert inbox.seq==0 and not inbox.items


def test_candidate_distinct_items_and_repeated_evidence():
    c=load_candidate('factorylab/runtime/continuity.py');ledger,clock,store=cold.archive()
    inbox=c.OutcomeInbox(store,ledger,lambda:clock.ns)
    for i in range(10):inbox.append('alice',handle='d1',outcome={'i':i},evidence=f'fact{i}')
    clock.ns=50;inbox.append('alice',handle='d1',outcome={'i':0},evidence='fact0')
    shown=inbox.unread('alice');assert shown['count']==10 and shown['items'][0]['outcome']['i']==0
    assert inbox.get('alice','outcome:1')['outcome']=={'i':0}
    assert len(inbox.get('alice','d1')['related_outcomes'])==10
    inbox.ack_through('alice',shown['items'][-1]['outcome_id'])
    assert inbox.unread('alice')['count']==2


def test_candidate_byte_time_is_invariant_to_shrinking_state():
    c=load_candidate('factorylab/runtime/continuity.py');notes=load_candidate('factorylab/runtime/notes.py')
    ledger,clock,store=cold.archive();state=c.WorkingState(store,ledger,lambda:clock.ns)
    old=state.put('alice',{'text':'x'*1000},handle='d1');clock.ns=notes.NS_PER_DAY
    new=state.put('alice',{},handle='d2');micro,carry=notes.accrue(new,clock.ns,notes.NotesSpec())
    num,den=notes.NotesSpec().rate()
    assert (micro,carry)==divmod(old['bytes']*clock.ns*num,den)


def test_candidate_artifact_grants_orphans_and_durable_file(tmp_path):
    a=load_candidate('factorylab/kernel/artifacts.py');ledger=cold.Evidence()
    store=a.ArtifactStore(ledger,root=tmp_path,clock_ns=lambda:0)
    sha=store.put(b'same',owner='alice',kind='working.state')
    store.put(b'same',owner='bob',kind='working.state')
    assert store.read(sha,reader='bob')['text']=='same'
    store.put(b'same',owner='bob',kind='note',public=True)
    assert store.read(sha,reader='carol')['text']=='same'
    store.index.clear();assert store.read(sha,reader='carol')['error']==a.PRIVATE_REFUSAL
    ledger.fail_kind='artifact.put'
    with pytest.raises(OSError):store.put(b'orphan',owner='alice',kind='working.state')
    assert not store.index


def test_candidate_public_projection_and_minimal_world_privacy():
    c=load_candidate('factorylab/cortex/request.py')
    private={'action':'hold','working_state':{'secret':1},'ack_through':'d1','raw':'secret','propensity':{'hold':1}}
    assert c.public_return(private)=={'action':'hold','propensity':{'hold':1}}
    req=c.Request('d1','work',{'you':'alice','world':{'seats':[
        {'seat_id':'alice','your_resources':{'mine':1}},
        {'seat_id':'bob','your_resources':{'secret':'BOB_SECRET'}}]}},{},{},10,1,None,'done','verdict','alice')
    assert 'BOB_SECRET' not in req.prompt_text()


def test_candidate_router_abstention_does_not_manufacture_a_producer():
    from factorylab.kernel.queue import SettleStatus
    called=[];settled=[]
    fn=cold.method_from_source('factorylab/runtime/loop.py','Runtime','_assembly_step',
        {'NOOP':'noop','SettleStatus':SettleStatus,'DEF_VERDICT':'verdict'},
        candidate_sources()['factorylab/runtime/loop.py'])
    rt=SimpleNamespace(_start_return=lambda h:None,_event_subject=lambda ev:None,
        _producer_step=lambda *a:called.append(a),stats=SimpleNamespace(noops=0),
        consequences=SimpleNamespace(finish=lambda *a:None),
        queue=SimpleNamespace(get=lambda h:SimpleNamespace(channel='v'),settle=lambda *a,**kw:settled.append(kw)))
    fn(rt,object(),'d1',SimpleNamespace(chosen='noop'),10)
    assert not called and settled[0]['status']==SettleStatus.INAPPLICABLE


def test_candidate_kill_survives_ledger_failure_and_does_not_repeat():
    v=load_candidate('factorylab/runtime/venue.py');term=SimpleNamespace(final=False)
    term.kill=lambda reason:setattr(term,'final',True)
    rt=SimpleNamespace(m=SimpleNamespace(kill=SimpleNamespace(wind_down=True,dust_micro=1)),
        exchange=cold.FakeVenue(),ledger=cold.Evidence('kill.wind_down'),termination=term)
    report=v.VenueMixin.kill(rt,'explicit_kill');assert term.final and report['exposure_status']=='unknown'
    calls=rt.exchange.close_calls;v.VenueMixin.kill(rt,'explicit_kill');assert rt.exchange.close_calls==calls


def test_candidate_retired_seat_does_not_reclaim_headship():
    b=load_candidate('factorylab/kernel/budget.py');ledger=cold.Ledger();wallet=cold.Wallet(100000,ledger)
    book=b.BudgetBook(wallet,ledger,clock_ns=lambda:0);book.genesis(['alice','bob']);book.retire('alice','retire')
    assert book.credit('alice',1,'late')==0
    book.earn('alice',1,'late-service');assert 'alice' not in book.seats() and 'alice' not in book.heads()


def test_candidate_fidelity_does_not_score_the_challenged_proxy_against_itself():
    c=load_candidate('factorylab/settlement/settle.py')
    from factorylab.settlement.scoring import PrevalenceBaseline
    from factorylab.settlement.standing import ConsequenceStanding
    from factorylab.settlement.fidelity import FidelityObjection
    settler=c.Settler(None,None,ConsequenceStanding(min_coverage=0),PrevalenceBaseline(),None)
    settler._Settler__objections['j']=FidelityObjection('fidelity','proxy','counterexample',0)
    result=settler.settle_verdict(evaluator_id='alice',about_handle='d1',q=1,share=0,judge_handle='j')
    assert result.objection_brier is None and result.objection is not None


def test_candidate_income_reference_is_idempotent_and_conflicts_fail_closed():
    earn=cold.method_from_source('factorylab/world/treasury.py','Treasury','earn',
                                source=candidate_sources()['factorylab/world/treasury.py'])
    t=SimpleNamespace(ledger=cold.Evidence(),income={'earned_micro':0})
    assert earn(t,'service',1000,'same-tx',payer='buyer') is not None
    assert earn(t,'service',1000,'same-tx',payer='buyer') is None
    assert t.income['earned_micro']==1000 and len(t.ledger.rows)==1
    with pytest.raises(ValueError):earn(t,'service',2000,'same-tx',payer='buyer')
