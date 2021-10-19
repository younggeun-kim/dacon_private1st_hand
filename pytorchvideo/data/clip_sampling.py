# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.

import random
from abc import ABC, abstractmethod
from typing import Any, Dict, NamedTuple, Optional, Tuple


class ClipInfo(NamedTuple):
    """
    Named-tuple for clip information with:
        clip_start_sec  (float): clip start time.
        clip_end_sec (float): clip end time.
        clip_index (int): clip index in the video.
        aug_index (int): augmentation index for the clip. Different augmentation methods
            might generate multiple views for the same clip.
        is_last_clip (bool): a bool specifying whether there are more clips to be
            sampled from the video.
    """

    clip_start_sec: float
    clip_end_sec: float
    clip_index: int
    aug_index: int
    is_last_clip: bool


class ClipSampler(ABC):
    """
    Interface for clip samplers that take a video time, previous sampled clip time,
    and returns a named-tuple ``ClipInfo``.
    """

    def __init__(self, clip_duration: float) -> None:
        self._clip_duration = clip_duration
        self._current_clip_index = 0
        self._current_aug_index = 0

    @abstractmethod
    def __call__(
        self, last_clip_time: float, video_duration: float, annotation: Dict[str, Any]
    ) -> ClipInfo:
        pass


def make_clip_sampler(sampling_type: str, *args) -> ClipSampler:
    """
    Constructs the clip samplers found in ``pytorchvideo.data.clip_sampling`` from the
    given arguments.

    Args:
        sampling_type (str): choose clip sampler to return. It has three options:

            * uniform: constructs and return ``UniformClipSampler``
            * random: construct and return ``RandomClipSampler``
            * constant_clips_per_video: construct and return ``ConstantClipsPerVideoSampler``

        *args: the args to pass to the chosen clip sampler constructor.
    """
    if sampling_type == "uniform":
        return UniformClipSampler(*args)
    elif sampling_type == "random":
        return RandomClipSampler(*args)
    elif sampling_type == "constant_clips_per_video":
        return ConstantClipsPerVideoSampler(*args)
    else:
        raise NotImplementedError(f"{sampling_type} not supported")


class UniformClipSampler(ClipSampler):
    """
    Evenly splits the video into clips of size clip_duration.
    """

    def __init__(
        self,
        clip_duration: float,
        stride: Optional[float] = None,
        backpad_last: bool = False,
        eps: float = 1e-6,
    ):
        """
        Args:
            clip_duration (float):
                The length of the clip to sample (in seconds)
            stride (float, optional):
                The amount of seconds to offset the next clip by

                default value of None is equivalent to no stride => stride == clip_duration
            eps (float):
                Epsilon for floating point comparisons. Used to check the last clip.
            backpad_last (bool):
                Whether to include the last frame(s) by "back padding".

                For instance, if we have a video of 39 frames (30 fps = 1.3s)
                with a stride of 16 (0.533s) with a clip duration of 32 frames
                (1.0667s). The clips will be (in frame numbers):

                with backpad_last = False
                - [0, 32]

                with backpad_last = True
                - [0, 32]
                - [8, 40], this is "back-padded" from [16, 48] to fit the last window
        """
        super().__init__(clip_duration)
        self._stride = stride if stride is not None else clip_duration
        self._eps = eps
        self._backpad_last = backpad_last

        assert (
            self._stride > 0 and self._stride <= clip_duration
        ), f"stride must be >0 and <= clip_duration ({clip_duration})"

    def _clip_start_end(
        self, last_clip_time: float, video_duration: float, backpad_last: bool
    ) -> Tuple[float, float]:
        """
        Helper to calculate the start/end clip with backpad logic
        """
        clip_start = max(last_clip_time - max(0, self._clip_duration - self._stride), 0)
        clip_end = clip_start + self._clip_duration

        if backpad_last:
            buffer_amount = max(0.0, clip_end - video_duration)
            clip_start -= buffer_amount
            clip_start = max(0, clip_start)  # handle rounding
            clip_end = clip_start + self._clip_duration

        return clip_start, clip_end

    def __call__(
        self, last_clip_time: float, video_duration: float, annotation: Dict[str, Any]
    ) -> ClipInfo:
        """
        Args:
            last_clip_time (float): the last clip end time sampled from this video. This
                should be 0.0 if the video hasn't had clips sampled yet.
            video_duration: (float): the duration of the video that's being sampled in seconds
            annotation (Dict): Not used by this sampler.
        Returns:
            clip_info: (ClipInfo): includes the clip information (clip_start_time,
            clip_end_time, clip_index, aug_index, is_last_clip), where the times are in
            seconds and is_last_clip is False when there is still more of time in the video
            to be sampled.
        """
        clip_start, clip_end = self._clip_start_end(
            last_clip_time, video_duration, backpad_last=self._backpad_last
        )

        # if they both end at the same time - it's the last clip
        _, next_clip_end = self._clip_start_end(
            clip_end, video_duration, backpad_last=self._backpad_last
        )
        if self._backpad_last:
            is_last_clip = abs(next_clip_end - clip_end) < self._eps
        else:
            is_last_clip = next_clip_end > video_duration

        clip_index = self._current_clip_index
        self._current_clip_index += 1

        return ClipInfo(clip_start, clip_end, clip_index, 0, is_last_clip)


class RandomClipSampler(ClipSampler):
    """
    Randomly samples clip of size clip_duration from the videos.
    """

    def __call__(
        self, last_clip_time: float, video_duration: float, annotation: Dict[str, Any]
    ) -> ClipInfo:
        """
        Args:
            last_clip_time (float): Not used for RandomClipSampler.
            video_duration: (float): the duration (in seconds) for the video that's
                being sampled
            annotation (Dict): Not used by this sampler.
        Returns:
            clip_info (ClipInfo): includes the clip information of (clip_start_time,
            clip_end_time, clip_index, aug_index, is_last_clip). The times are in seconds.
            clip_index, aux_index and is_last_clip are always 0, 0 and True, respectively.

        """
        max_possible_clip_start = max(video_duration - self._clip_duration, 0)
        clip_start_sec = random.uniform(0, max_possible_clip_start)
        return ClipInfo(
            clip_start_sec, clip_start_sec + self._clip_duration, 0, 0, True
        )


class ConstantClipsPerVideoSampler(ClipSampler):
    """
    Evenly splits the video into clips_per_video increments and samples clips of size
    clip_duration at these increments.
    """

    def __init__(
        self, clip_duration: float, clips_per_video: int, augs_per_clip: int = 1
    ) -> None:
        super().__init__(clip_duration)
        self._clips_per_video = clips_per_video
        self._augs_per_clip = augs_per_clip

    def __call__(
        self, last_clip_time: float, video_duration: float, annotation: Dict[str, Any]
    ) -> ClipInfo:
        """
        Args:
            last_clip_time (float): Not used for ConstantClipsPerVideoSampler.
            video_duration: (float): the duration (in seconds) for the video that's
                being sampled.
            annotation (Dict): Not used by this sampler.
        Returns:
            a named-tuple `ClipInfo`: includes the clip information of (clip_start_time,
                clip_end_time, clip_index, aug_index, is_last_clip). The times are in seconds.
                is_last_clip is True after clips_per_video clips have been sampled or the end
                of the video is reached.

        """
        max_possible_clip_start = max(video_duration - self._clip_duration, 0)
        uniform_clip = max_possible_clip_start / self._clips_per_video
        clip_start_sec = uniform_clip * self._current_clip_index
        clip_index = self._current_clip_index
        aug_index = self._current_aug_index

        self._current_aug_index += 1
        if self._current_aug_index >= self._augs_per_clip:
            self._current_clip_index += 1
            self._current_aug_index = 0

        # Last clip is True if sampled self._clips_per_video or if end of video is reached.
        is_last_clip = False
        if (
            self._current_clip_index >= self._clips_per_video
            or uniform_clip * self._current_clip_index > max_possible_clip_start
        ):
            self._current_clip_index = 0
            is_last_clip = True

        return ClipInfo(
            clip_start_sec,
            clip_start_sec + self._clip_duration,
            clip_index,
            aug_index,
            is_last_clip,
        )
