from core import Parrot
from .telephone import telephone

def setup(bot: Parrot):
    bot.add_cog(telephone(bot))