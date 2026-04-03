"""Shared helper functions for hybrid GEST tool tests."""


def _init_story(building_tools):
    """Helper: create story to get to STORY_CREATED state."""
    r = building_tools["create_story"].invoke({
        "title": "TestStory", "narrative": "A test story."
    })
    assert "story_id" in r, f"create_story failed: {r}"
    return r["story_id"]


def _start_kitchen_scene(building_tools, actor_ids, scene_id="scene_1"):
    """Helper: start a scene in house9 kitchen."""
    r = building_tools["start_scene"].invoke({
        "scene_id": scene_id,
        "action_name": "KitchenActivity",
        "narrative": "Activity in the kitchen.",
        "episode": "house9",
        "region": "kitchen",
        "actor_ids": actor_ids,
    })
    assert "error" not in r, f"start_scene failed: {r}"
    return r


def _start_round(building_tools, setup=False):
    """Helper: start a round."""
    r = building_tools["start_round"].invoke({"setup": setup})
    assert r.get("success") is True, f"start_round failed: {r}"
    return r


def _start_poi_chain(building_tools, actor_id, episode, poi_index):
    """Helper: start chain at POI and continue with first action."""
    r = building_tools["start_chain"].invoke({
        "actor_id": actor_id, "episode": episode, "poi_index": poi_index
    })
    assert "next_actions" in r, f"start_chain failed: {r}"
    assert len(r["next_actions"]) > 0, f"No actions at POI {poi_index}: {r}"
    first_action = r["next_actions"][0]
    r2 = building_tools["continue_chain"].invoke({
        "actor_id": actor_id, "next_action": first_action
    })
    assert "event_id" in r2, f"continue_chain with {first_action} failed: {r2}"
    return r2


def _start_spawnable(building_tools, actor_id, spawnable_type):
    """Helper: start chain and begin spawnable. Creates atomic start events.
    MobilePhone: AnswerPhone (creates TakeOut+AnswerPhone+TalkPhone)
    Cigarette: StartSmoking (creates TakeOut+SmokeIn+Smoke)"""
    r = building_tools["start_chain"].invoke({"actor_id": actor_id})
    assert "next_actions" in r, f"start_chain failed: {r}"
    start_action = 'AnswerPhone' if spawnable_type == 'MobilePhone' else 'StartSmoking'
    r2 = building_tools["continue_chain"].invoke({
        "actor_id": actor_id, "next_action": start_action
    })
    assert "event_id" in r2, f"{start_action} failed: {r2}"
    return r2


def _complete_spawnable(building_tools, actor_id, spawnable_type):
    """Helper: end spawnable and end chain. Creates atomic end events.
    MobilePhone: HangUp (creates HangUp+Stash)
    Cigarette: StopSmoking (creates SmokeOut+Stash)"""
    end_action = 'HangUp' if spawnable_type == 'MobilePhone' else 'StopSmoking'
    r = building_tools["continue_chain"].invoke({
        "actor_id": actor_id, "next_action": end_action
    })
    assert "event_id" in r, f"{end_action} failed: {r}"
    end = building_tools["end_chain"].invoke({"actor_id": actor_id})
    assert end.get("success") is True, f"end_chain failed: {end}"


def _end_round(building_tools):
    """Helper: end a round."""
    r = building_tools["end_round"].invoke({})
    assert r.get("success") is True, f"end_round failed: {r}"
    return r


def _end_scene(building_tools):
    """Helper: end the current scene."""
    r = building_tools["end_scene"].invoke({})
    assert r.get("success") is True, f"end_scene failed: {r}"
    return r
