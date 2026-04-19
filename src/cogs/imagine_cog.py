"""
Imagine Cog — AI image generation via Stable Diffusion (Automatic1111).

Commands:
  /imagine generate — generate an image from a text prompt
  /imagine status   — check if Stable Diffusion is online and list models
"""

import base64
import io
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth
from config import cfg

log = logging.getLogger("openclaw")

DEFAULT_NEGATIVE = "blurry, low quality"

SIZE_MAP = {
    "small": (512, 512),
    "medium": (768, 768),
    "large": (1024, 1024),
}


class ImagineCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    imagine = app_commands.Group(name="imagine", description="AI image generation via Stable Diffusion")

    # ── /imagine generate ─────────────────────────────────────────────────

    @imagine.command(name="generate", description="Generate an image from a text prompt")
    @app_commands.describe(
        prompt="Describe what you want to generate",
        size="Output resolution: small (512), medium (768), large (1024)",
        negative="Things to exclude from the image (appended to defaults)",
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="small (512×512)", value="small"),
            app_commands.Choice(name="medium (768×768)", value="medium"),
            app_commands.Choice(name="large (1024×1024)", value="large"),
        ]
    )
    @require_auth()
    async def imagine_generate(
        self,
        interaction: discord.Interaction,
        prompt: str,
        size: str = "medium",
        negative: str = "",
    ) -> None:
        from cooldowns import check_cooldown

        remaining = check_cooldown("imagine", interaction.user.id, cooldown_seconds=10.0)
        if remaining > 0:
            await interaction.response.send_message(
                f"⏱ Please wait {remaining:.1f}s before generating another image.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await interaction.followup.send("🎨 Generating...")

        width, height = SIZE_MAP.get(size, SIZE_MAP["medium"])
        negative_prompt = f"{DEFAULT_NEGATIVE}, {negative}" if negative else DEFAULT_NEGATIVE

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "steps": 20,
            "width": width,
            "height": height,
            "cfg_scale": 7,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{cfg.sd_url}/sdapi/v1/txt2img",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            img_bytes = base64.b64decode(data["images"][0])
            file = discord.File(io.BytesIO(img_bytes), filename="generated.png")

            embed = discord.Embed(
                title="🎨 Image Generated",
                color=discord.Color.purple(),
            )
            embed.add_field(name="Prompt", value=prompt[:1024], inline=False)
            if negative:
                embed.add_field(name="Negative", value=negative[:512], inline=False)
            embed.add_field(name="Size", value=f"{width}×{height}", inline=True)
            embed.set_image(url="attachment://generated.png")

            await interaction.edit_original_response(content=None, attachments=[file], embed=embed)
            await audit_log(interaction, f"imagine generate: {prompt[:100]}")

        except aiohttp.ClientConnectorError:
            await interaction.edit_original_response(
                content=f"❌ Stable Diffusion is offline. Make sure it's running at `{cfg.sd_url}`"
            )
        except Exception:  # broad: intentional
            await interaction.edit_original_response(content="❌ Image generation failed. Check the logs for details.")

    # ── /imagine status ───────────────────────────────────────────────────

    @imagine.command(name="status", description="Check if Stable Diffusion is online and list models")
    @require_auth()
    async def imagine_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{cfg.sd_url}/sdapi/v1/sd-models",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    models = await resp.json()

            model_names = [m.get("model_name") or m.get("title", "unknown") for m in models]
            description = "\n".join(f"• `{name}`" for name in model_names) or "No models found."

            embed = discord.Embed(
                title="✅ Stable Diffusion Online",
                description=description[:4000],
                color=discord.Color.green(),
            )
            embed.set_footer(text=cfg.sd_url)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except aiohttp.ClientConnectorError:
            embed = discord.Embed(
                title="❌ Stable Diffusion Offline",
                description=f"Could not connect to `{cfg.sd_url}`.\nMake sure the SD server is running.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:  # broad: intentional
            await interaction.followup.send("❌ Status check failed. Check the logs.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(ImagineCog(bot))
