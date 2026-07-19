from pathlib import Path

import fitz


OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "uploads"
    / "test-ebooks"
    / "the-lantern-at-low-tide-10-pages.pdf"
)


CHAPTERS = [
    (
        "Chapter One: The Sea Bell",
        """On the morning the sea bell rang without wind, Mara Vale was mending nets on the roof of her uncle's shop. Low Tide was a village built with one eye on the water and the other on the cliffs. Its people knew the moods of gulls, the color of approaching rain, and the hour when the harbor stones began to shine. They did not know what to make of a bell that had hung silent for thirty years.

The sound came from the old lighthouse beyond the western breakwater: one clear note, then another, carrying over chimneys and market stalls. Mara looked up before anyone else did. The lighthouse had been locked since the keeper disappeared, and the bell rope had rotted away long before Mara was born.

Her uncle called from below, asking whether the tide table had fallen from the counter again. Mara did not answer. Between the slate roofs she saw a single lantern burning in the lighthouse window, pale as a star refusing daylight. It blinked three times, paused, and blinked once more.

Her grandmother had taught her that pattern when Mara was small. It was not a warning of storms. It meant: come alone.

By noon, every adult in the village had found a reason not to speak of the bell. The harbor master said it was a trick of echoes. The baker said old buildings made old noises. Mara listened to their careful voices and understood that they were afraid of remembering. She took her canvas satchel, a coil of twine, and the small brass key her grandmother had left her. When the tide began to pull away from the quay, she walked toward the lighthouse.""",
    ),
    (
        "Chapter Two: A Map of Salt",
        """The path to the lighthouse appeared only at low tide. Flat stones rose from the seaweed like the backs of sleeping animals, and pools between them held minnows, crabs, and scraps of sky. Mara crossed slowly, using the twine to mark the safest turns. Behind her, Low Tide seemed smaller than it had that morning, a handful of houses holding their breath.

At the lighthouse door, the brass key turned without resistance. Inside, the air smelled of salt, lamp oil, and something older: paper kept too long in a closed room. A spiral stair curled upward, but a fresh trail of damp footprints led down instead, through a narrow door Mara had never noticed in stories or sketches.

The basement held a wooden desk bolted to the floor. On it lay a map drawn on thick blue paper. The coast was familiar until Mara reached the western edge, where an island had been marked in silver ink. Beside it, someone had written: When the bell rings, the island returns.

The island was called Vey, though Mara had never seen it from the shore. Her grandmother used the name only in lullabies, always stopping before the final verse. Now the map showed a route through reefs and shifting channels, ending at a symbol shaped like a lantern flame.

Under the map was a note in a hand Mara recognized from old recipe cards. It was her grandmother's handwriting. Do not let them trade memory for safety again. Find the room beneath the light.

Mara folded the map before the sea air could take it. From somewhere above came the scrape of a chair. The lighthouse was not empty after all.""",
    ),
    (
        "Chapter Three: The Keeper's Room",
        """Mara climbed toward the sound with one hand on the cold rail. Each step was worn smooth at the center, as if the missing keeper had walked the same circle for a lifetime. At the top, the lantern room glowed with late afternoon light. The great lens stood dark, its glass panels clouded by dust.

In the corner sat a boy about Mara's age, wrapped in a coat much too large for him. He had a narrow face and a shaved patch above one ear, where a pale scar crossed his temple. He did not look surprised to see her.

"You heard it," he said.

Mara tightened her grip on the map. "Who are you?"

"Jonas. My father was the keeper before yours stopped being one."

Mara almost laughed. Her father had been a fisherman. He had vanished at sea when she was six. Yet Jonas pointed to a framed photograph on the wall: two young men beside the lighthouse lens, both smiling into the wind. One was unmistakably her father.

Jonas explained in fragments. Every generation, the village sent two keepers to watch Vey. The island was not dangerous by itself. What it held was dangerous: a room that could remove a memory from anyone willing to surrender it. Years ago, the village had used it after a terrible storm. They had erased the storm, the people lost in it, and the promise they made to keep Vey sealed.

"The bell brings the island back," Jonas said. "This time, someone wants the room open."

Outside the glass, fog gathered where the horizon should have been. A dark line rose through it: an island that had not existed that morning.""",
    ),
    (
        "Chapter Four: The Broken Compass",
        """Jonas had a boat hidden in a cave below the lighthouse, a narrow skiff with patched sails and a compass that spun whenever it came near the western channel. Mara did not trust the boat, the compass, or Jonas, but Vey was already fading in and out of the fog. Waiting seemed like a decision too.

They launched at dusk. The water beyond the breakwater was strangely calm. Mara steered by the silver route on her grandmother's map while Jonas watched the compass needle whirl. Every few minutes he called out a memory, as if reciting it could keep it safe: the taste of his mother's pear jam, the sound of boots on the keeper's stairs, the name of the dog he had when he was five.

"Why do you do that?" Mara asked.

"Because the room starts taking small things before you enter it," he said.

She wanted to dismiss the idea, but then she realized she could no longer remember the color of the curtains in her childhood bedroom. The loss was tiny, almost ridiculous, yet it opened a cold space behind her ribs.

The compass cracked with a sharp sound. Its needle snapped loose and lay flat. In its place, under the glass, Mara saw a narrow strip of paper. She pried it out with her fingernail. Her grandmother had written only six words: Trust the tide, not the needle.

Mara lowered the sail. The skiff drifted with the outgoing current, not toward the island's visible shore, but toward a black gap between two reefs. Jonas stared at her as if she had gone mad. Then the fog opened, and a sheltered cove appeared where there had been only rock.

Vey was waiting for them.""",
    ),
    (
        "Chapter Five: The Island in Fog",
        """Vey had no birds, no grass, and no sound except water falling somewhere beneath the stone. A path of white shells led from the cove to a low building with no windows. The lighthouse on the mainland was visible behind them, but it looked impossibly far away, a pin of gold in a wall of gray.

At the building's entrance stood the harbor master of Low Tide. His coat was dry despite the fog, and he held a lantern with a blue flame. Mara had known him all her life as a gentle man who repaired children's kites. Here, on Vey, his face seemed borrowed.

"You should have stayed home," he said. "You are too young to understand what mercy costs."

Jonas stepped forward. "You opened the bell."

"I rang it because the village needs relief. We carry too much. Every family has a name they cannot say, every house has a room no one enters. The old bargain worked once. It can work again."

Mara thought of the silence after the bell, of adults changing the subject with frightened precision. She understood the temptation. To make grief disappear might feel like kindness. But her grandmother's note had not said memory was painful. It had said memory was safety.

The harbor master lifted the lantern. Behind him, the door opened without a touch. A staircase descended into blue light.

"The room only asks for what hurts," he said. "You may leave with peace."

Mara looked at Jonas. He was pale, but he shook his head. Together they stepped past the harbor master and into the building. The door closed behind them, leaving the island and its fog outside.""",
    ),
    (
        "Chapter Six: Names Written in Sand",
        """The stair ended in a round chamber cut from black stone. Its walls were covered with names, thousands of them, pressed into wet sand that never dried. Some names were clear. Others had begun to blur, their letters softened by an invisible tide.

In the center stood a shallow pool. The water reflected not their faces but moments from their lives. Mara saw her father lifting her onto his shoulders at the harbor festival. Then the image trembled. The pool showed him climbing into his fishing boat on the morning he disappeared, and Mara felt the room reaching for that day.

Jonas ran to the wall. He found his mother's name and placed both hands around it. "It is fading," he whispered.

The harbor master entered behind them. "Let it fade," he said. "She would want you free."

Mara knew then that the room did not take pain alone. It took the shape pain gave to love. If she surrendered the day her father left, she would also surrender the reason she still searched the horizon. If Jonas surrendered his mother, he would lose the proof that she had ever mattered.

She unfolded the map. In the lower corner, almost hidden under a fold, was a final instruction: Give the room a memory it cannot own.

Mara looked around the chamber. The walls held names from the past, but none from the present. The room fed on memories already formed, stories kept privately until they could be stolen.

She began to speak. Not to the pool, but to Jonas. She told him the whole story of the sea bell, the map, the broken compass, and their crossing. He added his own details, correcting her, laughing once despite himself. Their voices filled the chamber. The names on the walls stopped fading.

The harbor master's blue lantern flickered.""",
    ),
    (
        "Chapter Seven: The Night Crossing",
        """The chamber shook as if the island had taken a breath. Sand slid from the walls in thin streams. The harbor master raised his lantern, but the flame inside had turned white.

"Stories are not enough," he said. "You cannot keep every wound."

"No," Mara answered. "But we can keep each other."

She took the brass key from her pocket and dropped it into the pool. The water flashed. The lighthouse, the village, the people who had hidden from the bell, all appeared in its surface. Mara spoke their names aloud: the baker, the schoolteacher, her uncle, the old woman who sold shells near the quay. Jonas followed. With every name, a new line appeared in the sand.

The harbor master staggered. His face changed as memories returned to him: a daughter lost in the storm, a promise he had made beside the lighthouse, a night spent choosing forgetfulness because remembering felt impossible. He fell to his knees, holding the lantern as though it weighed more than stone.

Above them, a crack opened in the ceiling. Cold seawater began to pour through. The island was sinking with the room.

They ran. The stair twisted beneath their feet, and the blue light chased them upward. At the door, the harbor master did not follow. He stood at the threshold, staring toward the chamber below.

"Come with us," Jonas shouted.

The harbor master looked at Mara. "Someone has to close it."

Mara wanted to argue, but she saw that he was not choosing escape from memory this time. He was choosing to carry it.

They reached the cove as the skiff tore free from its mooring. Jonas jumped aboard and caught Mara's hand. Behind them, Vey cracked down the middle, and the fog spun into a black column. The tide seized the boat and pulled them toward home.""",
    ),
    (
        "Chapter Eight: The Room Beneath the Lighthouse",
        """They arrived at Low Tide before dawn. The sea bell was silent, but every light in the village was burning. People crowded the harbor in coats thrown over nightclothes. No one asked why Mara and Jonas had gone to Vey. They looked at the water and seemed to know.

Mara led them to the lighthouse basement. The hidden door stood open. Beyond it was another stair, older and narrower than the one on Vey. Her grandmother's warning returned to her: Find the room beneath the light.

At the bottom they found a small archive, not a second magic chamber. Shelves held ledgers, weather logs, letters, and photographs sealed in glass. The village had been keeping its memories all along, even after pretending to lose them.

Mara opened a ledger dated thirty years earlier. It recorded the storm in plain handwriting: the names of the boats, the names of the missing, the names of those who survived. On the last page was her father's signature, written before he became a fisherman again. He had not been a keeper by accident. He had left the lighthouse so Mara could have an ordinary childhood, but he had never stopped watching the sea.

Jonas found a letter from his mother. It explained that she had stayed on Vey to prevent the harbor master from reopening the room. She had not disappeared into a legend. She had made a choice, and someone had preserved the truth of it.

As the sun rose, the village gathered in the archive. One by one, people read the names they had avoided for decades. Some cried. Some stood very still. No magic made it easy. Yet the silence that had ruled Low Tide began to loosen, like a knot finally given patient hands.""",
    ),
    (
        "Chapter Nine: The Last Tide",
        """For three days, the western sea stayed rough. No boats went out, and the lighthouse lens remained dark. Mara and Jonas worked in the archive, sorting letters by year and carrying damp books into the sun. The villagers came in shifts. They brought old stories, corrected dates, and filled gaps in the ledgers with things they had once been afraid to say.

On the fourth evening, a figure appeared at the end of the breakwater. It was the harbor master, walking slowly with no lantern in his hand. His coat was torn, and his hair was white with salt. The crowd went quiet.

He did not offer an excuse. He told them he had sealed the room as Vey sank, and that the island would not return while the lighthouse kept its true record. Then he named his daughter. He named every person he had hoped to forget. His voice broke on the last name, but he finished.

Mara expected anger. There was some. There were questions that could not be answered before nightfall. But the baker stepped forward first and placed a hand on the harbor master's shoulder. "We remember with you," she said.

That became the village's answer. Not forgiveness without consequence, and not punishment without witness. They would remember together.

At low tide, Mara returned to the lighthouse roof. The sea bell hung above her, green with age. She replaced its missing rope with the twine from her satchel. Jonas held the ladder steady. When the knot was finished, Mara pulled once.

The bell rang over Low Tide, not as a command to hide, but as a signal heard by every boat beyond the harbor mouth: the light was tended, the records were open, and no one would be asked to disappear again.""",
    ),
    (
        "Chapter Ten: A Light for Morning",
        """By autumn, the lighthouse had a new keeper's room, though Mara refused the title whenever anyone offered it. She and Jonas called themselves caretakers instead. They repaired the lens, cleaned the archive, and taught schoolchildren how to read a tide chart before they learned to steer a boat.

The western horizon was empty. Vey did not return. Still, Mara kept the silver map in a drawer beneath the lantern controls. It reminded her that a safe place was not one without danger. It was a place where people agreed to face danger honestly.

Her uncle repaired the shop roof and pretended not to notice when she came home late. The harbor master resigned and spent his mornings recording oral histories from the oldest villagers. The baker began making pear jam from Jonas's mother's recipe. Each change was small enough to miss from a distance. Together they made Low Tide feel like a town waking from a long sleep.

One clear morning, Mara climbed the lighthouse stairs alone. The sea was bright below her, full of boats moving steadily toward open water. She lit the lamp, adjusted the lens, and watched the beam travel across the waves.

For a moment she imagined her father seeing it from far away. The thought hurt, but it no longer cut like a hidden shard. It belonged to her. She could carry it.

Then Jonas called from the stairs, saying the first school group had arrived and someone had brought a jar of pear jam. Mara smiled, closed the lantern room door behind her, and went down to meet the day.

Outside, the sea bell moved gently in the morning wind. It did not ring. It did not need to. The village was listening now.""",
    ),
]


def add_page(document: fitz.Document, chapter_number: int, heading: str, body: str) -> None:
    page = document.new_page(width=612, height=792)
    page.draw_rect(page.rect, color=None, fill=(0.975, 0.965, 0.93))
    page.draw_rect(fitz.Rect(54, 54, 558, 738), color=(0.75, 0.66, 0.48), width=0.8)
    page.insert_text(
        fitz.Point(70, 88),
        "THE LANTERN AT LOW TIDE",
        fontname="helv",
        fontsize=9,
        color=(0.28, 0.35, 0.38),
    )
    page.insert_textbox(
        fitz.Rect(70, 108, 542, 158),
        heading,
        fontname="times-bold",
        fontsize=19,
        lineheight=1.12,
        color=(0.08, 0.16, 0.2),
    )
    remaining = page.insert_textbox(
        fitz.Rect(70, 172, 542, 686),
        body,
        fontname="times-roman",
        fontsize=10.5,
        lineheight=1.34,
        color=(0.12, 0.14, 0.15),
        align=fitz.TEXT_ALIGN_JUSTIFY,
    )
    if remaining < 0:
        raise RuntimeError(f"Chapter {chapter_number} did not fit on one page.")
    page.draw_line(
        fitz.Point(70, 710), fitz.Point(542, 710), color=(0.75, 0.66, 0.48), width=0.5
    )
    page.insert_text(
        fitz.Point(70, 730),
        "Original test ebook for translation QA",
        fontname="helv",
        fontsize=8,
        color=(0.35, 0.4, 0.4),
    )
    page.insert_text(
        fitz.Point(520, 730),
        str(chapter_number),
        fontname="hebo",
        fontsize=9,
        color=(0.28, 0.35, 0.38),
    )


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    document.set_metadata(
        {
            "title": "The Lantern at Low Tide",
            "author": "AI Document Intelligence Platform",
            "subject": "Original English test ebook for translation quality testing",
        }
    )
    for number, (heading, body) in enumerate(CHAPTERS, start=1):
        add_page(document, number, heading, body)
    document.save(OUTPUT_PATH, garbage=4, deflate=True)
    document.close()
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
