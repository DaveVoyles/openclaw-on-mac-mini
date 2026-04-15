"""
Interview Cog — sequentially questions the user then produces tailored LLM output.

Commands:
  /interview  — bot asks 4 clarifying questions then synthesizes personalized output
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, truncate_for_embed
from discord_error import build_error_embed
from llm.chat import chat

log = logging.getLogger("openclaw.interview_cog")

# keyed by user_id
# Each session: {"goal": str, "questions": list[str], "answers": list[str]}
_sessions: dict[int, dict] = {}
_SESSION_MAX_SIZE = 100  # Maximum concurrent interview sessions


def _evict_oldest_session():
    """Remove the oldest session if we're at capacity."""
    if len(_sessions) >= _SESSION_MAX_SIZE:
        # Remove first (oldest) entry
        oldest_key = next(iter(_sessions))
        del _sessions[oldest_key]


# ── LLM helpers ───────────────────────────────────────────────────────────────

async def _generate_questions(goal: str) -> list[str]:
    prompt = (
        f'You are a skilled interviewer. The user wants to accomplish this goal: "{goal}"\n'
        "Generate exactly 4 short, open-ended clarifying questions that will help you create "
        "a better, more personalized output.\n"
        "Questions should be specific to the goal and not generic.\n"
        "Return ONLY the questions, one per line, no numbering, no extra text."
    )
    text, _, _ = await chat(prompt, model_preference="auto")
    return [line.strip() for line in text.strip().split("\n") if line.strip()][:5]


# ── Save-to-vault view ────────────────────────────────────────────────────────

class _InterviewOutputView(discord.ui.View):
    def __init__(self, *, output: str, goal: str):
        super().__init__(timeout=300)
        self.output = output
        self.goal = goal

    @discord.ui.button(label="💾 Save to Vault", style=discord.ButtonStyle.success)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            from obsidian_writer import save_to_vault

            result = await save_to_vault(
                title=f"Interview - {self.goal}",
                content=self.output,
                content_type="note",
                tags=["interview"],
                source_url="",
            )
            button.disabled = True
            button.label = "✅ Saved"
            await interaction.message.edit(view=self)
            await interaction.followup.send(f"📁 Saved to vault: {result}", ephemeral=True)
        except Exception as e:
            log.exception("interview vault save failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/interview save"), ephemeral=True)


# ── Modal ─────────────────────────────────────────────────────────────────────

class InterviewModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        cog: "InterviewCog",
        user_id: int,
        question: str,
        question_num: int,
        total: int,
    ):
        super().__init__(title=f"Question {question_num}/{total}", timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.question_num = question_num
        self.answer_input = discord.ui.TextInput(
            label=question[:45],  # Discord label max 45 chars
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer here...",
            max_length=1000,
            required=True,
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        session = _sessions.get(self.user_id)
        if not session:
            await interaction.response.send_message(
                "Session expired. Please run /interview again.", ephemeral=True
            )
            return

        session["answers"].append(self.answer_input.value)
        questions = session["questions"]
        next_q_idx = self.question_num  # 0-indexed next question

        if next_q_idx < len(questions):
            next_modal = InterviewModal(
                cog=self.cog,
                user_id=self.user_id,
                question=questions[next_q_idx],
                question_num=next_q_idx + 1,
                total=len(questions),
            )
            await interaction.response.send_modal(next_modal)
        else:
            # All questions answered — synthesize
            await interaction.response.defer(ephemeral=True)
            try:
                qa_pairs = list(zip(questions, session["answers"]))
                output, model = await self.cog._synthesize(session["goal"], qa_pairs)
                embed = discord.Embed(
                    title=f"📋 {session['goal'][:80]}",
                    description=truncate_for_embed(output),
                    color=discord.Color.green(),
                )
                embed.set_footer(text=f"Interview complete | via {model}")
                view = _InterviewOutputView(output=output, goal=session["goal"])
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                audit_log(
                    interaction.user,
                    "interview_complete",
                    {"goal": session["goal"], "model": model},
                )
            except Exception as e:
                log.exception("interview synthesize failed")
                await interaction.followup.send(embed=build_error_embed(e, context="/interview"), ephemeral=True)
            finally:
                _sessions.pop(self.user_id, None)


# ── Cog ───────────────────────────────────────────────────────────────────────

class InterviewCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _synthesize(self, goal: str, qa_pairs: list[tuple[str, str]]) -> tuple[str, str]:
        formatted = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa_pairs)
        prompt = (
            f"The user wants to: {goal}\n\n"
            "Here are their answers to your clarifying questions:\n"
            f"{formatted}\n\n"
            "Based on this information, produce a high-quality, personalized output that fully "
            "addresses their goal.\n"
            "Be specific — use the details they provided. Format it clearly with headers if appropriate."
        )
        text, _, model = await chat(prompt, model_preference="auto")
        return text, model

    @app_commands.command(
        name="interview",
        description="Bot asks you questions then produces tailored output",
    )
    @app_commands.describe(
        goal="What do you want to create or decide? E.g. 'write my bio', 'plan my week'"
    )
    async def interview(self, interaction: discord.Interaction, goal: str):
        # Clear any existing session for this user and start fresh
        if interaction.user.id in _sessions:
            _sessions.pop(interaction.user.id)

        questions = await _generate_questions(goal)
        if not questions:
            await interaction.response.send_message(
                "❌ Failed to generate questions.", ephemeral=True
            )
            return

        _evict_oldest_session()  # Ensure we don't exceed capacity
        _sessions[interaction.user.id] = {
            "goal": goal,
            "questions": questions,
            "answers": [],
        }
        audit_log(interaction.user, "interview_start", {"goal": goal})

        await interaction.response.send_modal(
            InterviewModal(
                cog=self,
                user_id=interaction.user.id,
                question=questions[0],
                question_num=1,
                total=len(questions),
            )
        )


async def setup(bot):
    await bot.add_cog(InterviewCog(bot))
