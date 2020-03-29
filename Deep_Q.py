import torch
from torch import nn
import torch.nn.functional as F

import gym
import numpy as np
import math
import cv2
from collections import deque, namedtuple
import random



env = gym.make('BeamRider-v0')
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ACTION_MEANING = {
#     0: "NOOP",
#     1: "FIRE",
#     2: "UP",
#     3: "RIGHT",
#     4: "LEFT",
#     5: "DOWN",
#     6: "UPRIGHT",
#     7: "UPLEFT",
#     8: "DOWNRIGHT",
#     9: "DOWNLEFT",
#     10: "UPFIRE",
#     11: "RIGHTFIRE",
#     12: "LEFTFIRE",
#     13: "DOWNFIRE",
#     14: "UPRIGHTFIRE",
#     15: "UPLEFTFIRE",
#     16: "DOWNRIGHTFIRE",
#     17: "DOWNLEFTFIRE",
# }

actions = [0, 1, 3, 4]
n_actions = 4
Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward', 'done'))


class ReplayMemory(object):

    def __init__(self, capacity):
        self.capacity = capacity
        self.memory = deque(maxlen=capacity)

    def push(self, *args):
        """Saves a transition."""
        self.memory.append(Transition(*args))

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

class DeepQNet(nn.Module):
    def __init__(self, h, w):
        super(DeepQNet, self).__init__()
        self.conv1 = nn.Conv2d(1, 16, kernel_size= 8, stride=4)
        self.conv2 = nn.Conv2d(16, 32, kernel_size=4, stride=2)

        def conv2d_size_out(size, kernel_size, stride):
            return (size - (kernel_size - 1) - 1) // stride + 1
        
        convw = conv2d_size_out(conv2d_size_out(w, 8, 4), 4, 2)
        convh = conv2d_size_out(conv2d_size_out(h, 8, 4), 4, 2)

        linear_input = convh * convw * 32
        self.fc1 = nn.Linear(linear_input, 256)
        self.out = nn.Linear(256, 4)


    
    def forward(self, x):
        x = F.relu(self.conv1(x.float()))
        x = F.relu(self.conv2(x))
        
        x = F.relu(self.fc1(x.view(x.size(0), -1)))
        x = self.out(x)
        return x



def convert_screen(screen):
    # This function simplifies the environment as color is not important
    # the top sides of the screen are also irrelevant as the agent will
    # get a reward directly from the environment and not by looking at
    # the score
    reshaped = cv2.resize(screen, (84, 110), interpolation=cv2.INTER_AREA)
    cropped = reshaped[20:104]

    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)

    # cv2.imshow("cropped", gray)
    # cv2.waitKey(0)
    # We reshape as pytorch uses the order of features (CHW)
    return gray.reshape(1, HEIGHT, WIDTH)


BATCH_SIZE = 128
GAMMA = 0.999
EPS_START = 0.9
EPS_END = 0.05
EPS_DECAY = 200
TARGET_UPDATE = 10

HEIGHT = 84
WIDTH = 84


policy_net = DeepQNet(HEIGHT, WIDTH).to(device)
target_net = DeepQNet(HEIGHT, WIDTH).to(device)

optimizer = torch.optim.RMSprop(policy_net.parameters())
memory = ReplayMemory(10000)
steps_done = 0

def select_action(state):
    global steps_done
    # This equation is for the decaying epsilon
    eps_threshold = EPS_END + (EPS_START - EPS_END) * \
        math.exp(-1. * steps_done / EPS_DECAY)
    steps_done += 1
    r = np.random.rand()

    # We select an action with an espilon greedy policy 
    if r > eps_threshold:
        with torch.no_grad():
            # Return the action with the maximum Q value for the current state
            return policy_net(state).max(1)[1].view(1, 1)
    else:
        return torch.tensor([[random.randrange(n_actions)]], device=device)


def optimize_model():
    if len(memory.memory) < BATCH_SIZE:
        return
    transitions = memory.sample(BATCH_SIZE)
    # Transpose the batch (see https://stackoverflow.com/a/19343/3343043 for
    # detailed explanation). This converts batch-array of Transitions
    # to Transition of batch-arrays.
    batch = Transition(*zip(*transitions))

    # Compute a mask of non-final states and concatenate the batch elements
    # (a final state would've been the one after which simulation ended)
    non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                            batch.next_state)), device=device, dtype=torch.bool)
    non_final_next_states = torch.cat([s for s in batch.next_state
                                       if s is not None])
    state_batch = torch.cat(batch.state)
    action_batch = torch.cat(batch.action)
    reward_batch = torch.cat(batch.reward)

    # Compute Q(s_t, a) - the model computes Q(s_t), then we select the
    # columns of actions taken. These are the actions which would've been taken
    # for each batch state according to policy_net
    state_action_values = policy_net(state_batch).gather(1, action_batch)

    # Compute V(s_{t+1}) for all next states.
    # Expected values of actions for non_final_next_states are computed based
    # on the "older" target_net; selecting their best reward with max(1)[0].
    # This is merged based on the mask, such that we'll have either the expected
    # state value or 0 in case the state was final.
    next_state_values = torch.zeros(BATCH_SIZE, device=device)
    next_state_values[non_final_mask] = target_net(
        non_final_next_states).max(1)[0].detach()
    # Compute the expected Q values
    expected_state_action_values = (next_state_values * GAMMA) + reward_batch

    # Compute Huber loss
    loss = F.smooth_l1_loss(state_action_values,
                            expected_state_action_values.unsqueeze(1))

    # Optimize the model
    optimizer.zero_grad()
    loss.backward()
    for param in policy_net.parameters():
        param.grad.data.clamp_(-1, 1)
    optimizer.step()

PATH = "./deepQ.pt"

def train_model(num_episodes):
    cumulative_frames = 0
    for episode in range(num_episodes):
        print(episode)
        state = env.reset()
        state = convert_screen(state)
        state = state.reshape(-1, 1, 84, 84)
        state = torch.tensor(state, device=device)
        done = False

        cum_reward = 0
        while not done:
            action = select_action(state)

            next_state, reward, done, _ = env.step(actions[action.item()])
            next_state = torch.tensor(convert_screen(next_state).reshape(-1, 1, 84, 84), device=device)
            reward = torch.tensor([reward], device=device)

            if done:
                next_state = None
            
            memory.push(state, action, next_state, reward, done)


            state = next_state
            optimize_model()
            cum_reward += reward
            cumulative_frames += 1
        print("Final reward: {}".format(cum_reward.item()))
        print("Cumulative Frames: {}".format(cumulative_frames))
        if episode % TARGET_UPDATE == 0:
            target_net.load_state_dict(policy_net.state_dict())

        torch.save(target_net.state_dict(), PATH)

def load_agent():
    model = DeepQNet(HEIGHT, WIDTH).to(device)
    model.load_state_dict(torch.load(PATH, map_location=torch.device('cpu')))
    model.eval()
    return model


def inference(episodes, model):
    for episode in range(episodes):
        observation = env.reset()
        done = False
        while not done:
            env.render()
            with torch.no_grad():
                state = torch.tensor(convert_screen(
                    observation).reshape(-1, 1, 84, 84), device=device)
                r = np.random.rand()
                if r < 0.9:
                    action = actions[model(state).max(1)[1].view(1, 1).item()]
                else:
                    action = np.random.choice(actions)
                observation, _, done, _ = env.step(action)


def main():
    # train_model(300)
    model = load_agent()
    inference(100, model)



if __name__ == '__main__':
    main()
        
    





# for i_episode in range(100):
#     observation = env.reset()
#     done = False
#     print("HERE")

#     while not done:
#         env.render()    
#         action = env.action_space.sample()
#         # action = np.random.randint(3,5)
#         observation, reward, done, info = env.step(action)
#         if reward > 0:
#             print(reward)
#         elif reward < 0:
#             print(reward)
#         if done:
#             print("Episode finished")
# env.close()