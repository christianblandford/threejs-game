// The steward of the town's hall. He stands on the dais (the Empty `steward`
// in tools/blender/make_town_hall.py, with `talk = "steward"`).
//
// `fact` and `set_fact` read and write the saved world (src/engine/worldState.ts):
// the gate's lever writes "town/gate.lowered", and this story reads it.

EXTERNAL fact(name)
EXTERNAL set_fact(name, value)

// 0: never asked.  1: asked to lower the gate.  2: thanked for it.
VAR errand = 0

=== start ===
{ errand:
- 0: -> meeting
- 1: -> waiting
- else: -> afterwards
}

=== meeting ===
{ meeting > 1: You again. | A visitor. We get few, these days. } # speaker: Steward
I keep this hall, and I keep its accounts. What brings you up the hill?
-> questions

= questions
+ [Who are you?]
    The steward. I see the fires lit, the tables laid and the gate shut at dusk. Or I did, when there was anyone to do it with.
    -> questions
+ [Is there anything I can do?]
    -> ask
+ [Just looking around.]
    Look all you like. Mind the hearth.
    -> END

= ask
{ fact("town/gate.lowered"):
    There was. The gate wanted lowering, and I see from here that somebody has already done it. You?
    ~ errand = 2
    ~ set_fact("town/steward.thanked", true)
    Then the town owes you a quiet night. Thank you.
    -> END
}
There is. The gate stands open, and the man who worked it has gone to the coast.
The lever is just inside the gatehouse, on the left as you come in. Lower it for me, and come back.
~ errand = 1
-> END

=== waiting ===
{ fact("town/gate.lowered"):
    I heard it come down from here. Well done. # speaker: Steward
    ~ errand = 2
    ~ set_fact("town/steward.thanked", true)
    The town owes you a quiet night.
    -> END
}
The gate is still up. # speaker: Steward
+ [Where is the lever again?]
    Inside the gatehouse, on the left as you come in from the road. Pull it and the gate comes down.
    -> END
+ [I'm going.]
    Good.
    -> END

=== afterwards ===
{ fact("town/gate.lowered"):
    All quiet. As it should be. # speaker: Steward
- else:
    Somebody has raised the gate again. I shall pretend I did not notice. # speaker: Steward
}
+ [Good night.]
    And to you.
    -> END
+ [Tell me about the hall.]
    Older than the walls. The roof was raised by shipwrights, which is why it looks like a boat turned over.
    -> END
