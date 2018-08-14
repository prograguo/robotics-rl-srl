import numpy as np
from stable_baselines.ddpg import DDPG
from stable_baselines.ddpg.noise import AdaptiveParamNoiseSpec, NormalActionNoise, OrnsteinUhlenbeckActionNoise
from stable_baselines.common.policies import MlpPolicy, CnnPolicy
from stable_baselines.common.vec_env.vec_normalize import VecNormalize
from stable_baselines.common.vec_env.dummy_vec_env import DummyVecEnv

from rl_baselines.base_classes import StableBaselinesRLObject
from environments.utils import makeEnv
from rl_baselines.utils import WrapFrameStack, loadRunningAverage, MultiprocessSRLModel


class DDPGModel(StableBaselinesRLObject):
    """
    object containing the interface between baselines.ddpg and this code base
    DDPG: Deep Deterministic Policy Gradients
    """
    def __init__(self):
        super(DDPGModel, self).__init__(name="ddpg", model_class=DDPG)

    def customArguments(self, parser):
        parser.add_argument('--memory-limit',
                            help='Used to define the size of the replay buffer (in number of observations)', type=int,
                            default=100000)
        parser.add_argument('--noise-action',
                            help='The type of action noise added to the output, can be gaussian or OrnsteinUhlenbeck',
                            type=str, default="ou", choices=["none", "normal", "ou"])
        parser.add_argument('--noise-action-sigma', help='The variance of the action noise', type=float, default=0.2)
        parser.add_argument('--noise-param', help='Enable parameter noise', action='store_true', default=False)
        parser.add_argument('--noise-param-sigma', help='The variance of the parameter noise', type=float, default=0.2)
        parser.add_argument('--no-layer-norm', help='Disable layer normalization for the neural networks',
                            action='store_true', default=False)
        parser.add_argument('--batch-size',
                            help='The batch size used for training (use 16 for raw pixels and 64 for srl_model)',
                            type=int,
                            default=64)
        return parser

    @classmethod
    def makeEnv(cls, args, env_kwargs=None, load_path_normalise=None):
        # Even though DeepQ is single core only, we need to use the pipe system to work
        if env_kwargs is not None and env_kwargs.get("use_srl", False):
            srl_model = MultiprocessSRLModel(1, args.env, env_kwargs)
            env_kwargs["state_dim"] = srl_model.state_dim
            env_kwargs["srl_pipe"] = srl_model.pipe

        env = DummyVecEnv([makeEnv(args.env, args.seed, 0, args.log_dir, env_kwargs=env_kwargs)])

        if args.srl_model != "raw_pixels":
            env = VecNormalize(env)
            env = loadRunningAverage(env, load_path_normalise=load_path_normalise)

        # Normalize only raw pixels
        # WARNING: when using framestacking, the memory used by the replay buffer can grow quickly
        return WrapFrameStack(env, args.num_stack, normalize=args.srl_model == "raw_pixels")

    def train(self, args, callback, env_kwargs=None, train_kwargs=None):
        env = self.makeEnv(args, env_kwargs=env_kwargs)

        if train_kwargs is None:
            train_kwargs = {}

        # Parse noise_type
        action_noise = None
        param_noise = None
        n_actions = env.action_space.shape[-1]
        if args.noise_param:
            param_noise = AdaptiveParamNoiseSpec(initial_stddev=args.noise_param_sigma,
                                                 desired_action_stddev=args.noise_param_sigma)

        if args.noise_action == 'normal':
            action_noise = NormalActionNoise(mean=np.zeros(n_actions),
                                             sigma=args.noise_action_sigma * np.ones(n_actions))
        elif args.noise_action == 'ou':
            action_noise = OrnsteinUhlenbeckActionNoise(mean=np.zeros(n_actions),
                                                        sigma=args.noise_action_sigma * np.ones(n_actions))

        # get the associated policy for the architecture requested
        if args.srl_model == "raw_pixels":
            args.policy = "cnn"
        else:
            args.policy = "mlp"

        self.policy = args.policy
        self.ob_space = env.observation_space
        self.ac_space = env.action_space

        policy_fn = {'cnn': CnnPolicy,
                     'mlp': MlpPolicy}[args.policy]

        param_kwargs = {
            "verbose": 1,
            "nb_epochs": 500,
            "nb_epoch_cycles": 20,
            "render_eval": False,
            "render": False,
            "reward_scale": 1.,
            "param_noise": param_noise,
            "normalize_returns": False,
            "normalize_observations": (args.srl_model == "raw_pixels"),
            "critic_l2_reg": 1e-2,
            "actor_lr": 1e-4,
            "critic_lr": 1e-3,
            "action_noise": action_noise,
            "enable_popart": False,
            "gamma": 0.99,
            "clip_norm": None,
            "nb_train_steps": 100,
            "nb_rollout_steps": 100,
            "nb_eval_steps": 50,
            "batch_size": args.batch_size
        }

        self.model = self.model_class(policy_fn, env, {**param_kwargs, **train_kwargs})
        self.model.learn(total_timesteps=args.num_timesteps, seed=args.seed, callback=callback)
        env.close()
