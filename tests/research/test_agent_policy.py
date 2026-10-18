from test_agent_cycle import discovered

from epipilot.research.frontier import Phase


def test_phase_uses_resolved_uncertainty_not_evidence_volume(tmp_path):
    from epipilot.research.agent_policy import research_phase
    from epipilot.research.agent_service import reconcile_questions
    from epipilot.research.measurement import MeasurementAdapter

    engine, root, _batch = discovered(tmp_path)
    assert research_phase(engine.state) is Phase.EXPLORATION
    adapter = MeasurementAdapter(engine, root)
    engine.run_next(adapter, adapter)
    # Verified measurement alone is not yet a resolved unknown.
    assert research_phase(engine.state) is Phase.EXPLORATION
    reconcile_questions(engine)
    assert research_phase(engine.state) is Phase.OPTIMIZATION


def test_agent_packet_demands_discovery_after_new_result(tmp_path):
    from epipilot.research.agent_service import Action, AgentService

    engine, root, _batch = discovered(tmp_path)
    service = AgentService(engine, root)
    before = service.iterate()
    assert before.action is Action.EXECUTE
    assert before.proposal_id == "profile"
    service.run_next()
    after = service.iterate()
    assert after.action is Action.DISCOVER
    assert after.event_version > before.event_version
    assert not engine.state.research.active


def test_new_critical_unknown_reverts_phase_and_protects_exploration(tmp_path):
    from test_agent_discovery import batch_for

    from epipilot.research.agent_policy import rank_agent, research_phase
    from epipilot.research.agent_service import AgentService
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.frontier import Purpose, Rule
    from epipilot.research.models import Cost

    engine, root, old = discovered(tmp_path)
    AgentService(engine, root).run_next()
    assert research_phase(engine.state) is Phase.OPTIMIZATION
    batch = batch_for(engine, root, old.sources[0].revision)
    finding = batch.findings[0]
    finding = finding.model_copy(
        update={
            "topic_key": "recent-gap",
            "question": finding.question.model_copy(update={"topic_key": "recent-gap"}),
        }
    )
    explore = batch.proposals[0]
    c = explore.contract.model_copy(
        update={"proposal_id": "new-explore", "boundary_date": "2021-01-01"}
    )
    explore = explore.model_copy(
        update={
            "contract": c,
            "proposal": explore.proposal.model_copy(
                update={
                    "id": c.proposal_id,
                    "input_revision": c.fingerprint(),
                    "estimated_cost": (Cost(unit="wall_seconds", amount=4.0),),
                    "information": 0.01,
                    "contribution": 0.01,
                    "uncertainty": 0.01,
                    "risk": 0.0,
                }
            ),
        }
    )
    original = old.proposals[0]
    c = original.contract.model_copy(
        update={
            "proposal_id": "optimize",
            "purpose": Purpose.OPTIMIZE,
            "rules": (Rule(metric="rows", minimum=2.0),),
        }
    )
    opt = original.model_copy(
        update={
            "contract": c,
            "proposal": original.proposal.model_copy(
                update={
                    "id": c.proposal_id,
                    "input_revision": c.fingerprint(),
                    "estimated_cost": (Cost(unit="wall_seconds", amount=45.0),),
                    "information": 1.0,
                    "contribution": 1.0,
                    "uncertainty": 1.0,
                    "risk": 0.0,
                }
            ),
        }
    )
    apply_discovery(
        engine, root, batch.model_copy(update={"findings": (finding,), "proposals": (explore, opt)})
    )
    assert research_phase(engine.state) is not Phase.OPTIMIZATION
    ranked = rank_agent(engine.state, frozenset({"missingness"}))
    assert ranked.ranked[0].proposal_id == "new-explore"
    assert "protect declared exploration" in ranked.ranked[0].explanation


def test_unaffordable_child_cannot_add_lookahead_value(tmp_path):
    from test_agent_discovery import batch_for

    from epipilot.research.agent_policy import rank_agent
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.models import Cost

    engine, root, parent = discovered(tmp_path)
    batch = batch_for(engine, root, parent.sources[0].revision)
    f = batch.findings[0]
    f = f.model_copy(
        update={
            "topic_key": "child",
            "node": f.node.model_copy(update={"and_ids": (parent.findings[0].node.hypothesis_id,)}),
            "question": f.question.model_copy(update={"topic_key": "child"}),
        }
    )
    item = batch.proposals[0]
    c = item.contract.model_copy(update={"proposal_id": "child", "boundary_date": "2025-01-01"})
    item = item.model_copy(
        update={
            "contract": c,
            "proposal": item.proposal.model_copy(
                update={
                    "id": "child",
                    "input_revision": c.fingerprint(),
                    "estimated_cost": (Cost(unit="wall_seconds", amount=100.0),),
                }
            ),
        }
    )
    apply_discovery(engine, root, batch.model_copy(update={"findings": (f,), "proposals": (item,)}))
    ranking = rank_agent(engine.state, frozenset({"missingness"}))
    assert ranking.ranked[0].proposal_id == "profile"
    assert ranking.ranked[0].unlocking == 0
