from kaggle_environments import make
from main import agent

env = make("crawl", configuration={"randomSeed": 42}, debug=True)
env.run([agent, "random"])  # your agent vs the built-in random bot

# Print final result
final = env.steps[-1]
for i, s in enumerate(final):
    print(f"Player {i}: reward={s.reward}, status={s.status}")