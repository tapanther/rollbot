import discord
import json
import random

client = discord.Client()



@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('$hello'):
        await message.channel.send('Hello!')


if __name__ == '__main__':
    with open('env.json', 'r') as env_file:
        env = json.load(env_file)

    discord_token = env['TOKEN']
    client.run(discord_token)
