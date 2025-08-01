# BSD 2-Clause License
#
# Apprise - Push Notification Library.
# Copyright (c) 2025, Chris Caron <lead2gold@gmail.com>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from json import dumps

# Disable logging for a cleaner testing output
import logging
from unittest import mock

from helpers import AppriseURLTester
import pytest
import requests

from apprise import Apprise
from apprise.plugins.twist import NotifyTwist

logging.disable(logging.CRITICAL)

# Our Testing URLs
apprise_url_tests = (
    (
        "twist://",
        {
            # Missing Email and Login
            "instance": None,
        },
    ),
    (
        "twist://:@/",
        {
            "instance": None,
        },
    ),
    (
        "twist://user@example.com/",
        {
            # No password
            "instance": None,
        },
    ),
    (
        "twist://user@example.com/password",
        {
            # Password acceptable as first entry in path
            "instance": NotifyTwist,
            # Expected notify() response is False because internally we would
            # have failed to login
            "notify_response": False,
        },
    ),
    (
        "twist://password:user1@example.com",
        {
            # password:login acceptable
            "instance": NotifyTwist,
            # Expected notify() response is False because internally we would
            # have failed to login
            "notify_response": False,
            # Our expected url(privacy=True) startswith() response:
            "privacy_url": "twist://****:user1@example.com",
        },
    ),
    (
        "twist://password:user2@example.com",
        {
            # password:login acceptable
            "instance": NotifyTwist,
            # Expected notify() response is False because internally we would
            # have logged in, but we would have failed to look up the #General
            # channel and workspace.
            "requests_response_text": {
                # Login expected response
                "id": 1234,
                "default_workspace": 9876,
            },
            "notify_response": False,
        },
    ),
    (
        "twist://password:user2@example.com",
        {
            "instance": NotifyTwist,
            # throw a bizzare code forcing us to fail to look it up
            "response": False,
            "requests_response_code": 999,
        },
    ),
    (
        "twist://password:user2@example.com",
        {
            "instance": NotifyTwist,
            # Throws a series of i/o exceptions with this flag
            # is set and tests that we gracfully handle them
            "test_requests_exceptions": True,
        },
    ),
)


def test_plugin_twist_urls():
    """NotifyTwist() Apprise URLs."""

    # Run our general tests
    AppriseURLTester(tests=apprise_url_tests).run_all()


def test_plugin_twist_init():
    """NotifyTwist() init()"""
    with pytest.raises(TypeError):
        NotifyTwist(email="invalid", targets=None)

    with pytest.raises(TypeError):
        NotifyTwist(email="user@domain", targets=None)

    # Simple object initialization
    result = NotifyTwist(
        password="abc123", email="user@domain.com", targets=None
    )
    assert result.user == "user"
    assert result.host == "domain.com"
    assert result.password == "abc123"

    # Channel Instantiation by name
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel")
    assert isinstance(obj, NotifyTwist)

    # Channel Instantiation by id (faster if you know the translation)
    obj = Apprise.instantiate("twist://password:user@example.com/12345")
    assert isinstance(obj, NotifyTwist)

    # Invalid Channel - (max characters is 64), the below drops it
    obj = Apprise.instantiate(
        "twist://password:user@example.com/{}".format("a" * 65)
    )
    assert isinstance(obj, NotifyTwist)

    # No User detect
    result = NotifyTwist.parse_url("twist://example.com")
    assert result is None

    # test usage of to=
    result = NotifyTwist.parse_url(
        "twist://password:user@example.com?to=#channel"
    )
    assert isinstance(result, dict)
    assert "user" in result
    assert result["user"] == "user"
    assert "host" in result
    assert result["host"] == "example.com"
    assert "password" in result
    assert result["password"] == "password"
    assert "targets" in result
    assert isinstance(result["targets"], list) is True
    assert len(result["targets"]) == 1
    assert "#channel" in result["targets"]


@mock.patch("requests.get")
@mock.patch("requests.post")
def test_plugin_twist_auth(mock_post, mock_get):
    """NotifyTwist() login/logout()"""

    # Prepare Mock
    mock_get.return_value = requests.Request()
    mock_post.return_value = requests.Request()
    mock_post.return_value.status_code = requests.codes.ok
    mock_get.return_value.status_code = requests.codes.ok
    mock_post.return_value.content = dumps({
        "token": "2e82c1e4e8b0091fdaa34ff3972351821406f796",
        "default_workspace": 12345,
    })
    mock_get.return_value.content = mock_post.return_value.content

    # Instantiate an object
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel")
    assert isinstance(obj, NotifyTwist)
    # not logged in yet
    obj.logout()
    assert obj.login() is True

    # Clear our channel listing
    obj.channels.clear()
    # No channels mean there is no internal migration/lookups required
    assert obj._channel_migration() is True

    # Workspace Success
    mock_post.return_value.content = dumps([
        {
            "name": "TesT",
            "id": 1,
        },
        {
            "name": "tESt2",
            "id": 2,
        },
    ])
    mock_get.return_value.content = mock_post.return_value.content

    results = obj.get_workspaces()
    assert len(results) == 2
    assert "test" in results
    assert results["test"] == 1
    assert "test2" in results
    assert results["test2"] == 2

    mock_post.return_value.content = dumps([
        {
            "name": "ChaNNEL1",
            "id": 1,
        },
        {
            "name": "chaNNel2",
            "id": 2,
        },
    ])
    mock_get.return_value.content = mock_post.return_value.content
    results = obj.get_channels(wid=1)
    assert len(results) == 2
    assert "channel1" in results
    assert results["channel1"] == 1
    assert "channel2" in results
    assert results["channel2"] == 2

    # Test result failure response
    mock_post.return_value.status_code = 403
    mock_get.return_value.status_code = 403
    assert obj.get_workspaces() == {}

    # Return things how they were
    mock_post.return_value.status_code = requests.codes.ok
    mock_get.return_value.status_code = requests.codes.ok

    # Forces call to logout:
    del obj

    #
    # Authentication failures
    #
    mock_post.return_value.status_code = 403
    mock_get.return_value.status_code = 403

    # Instantiate an object
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel")
    assert isinstance(obj, NotifyTwist)

    # Authentication failed
    assert obj.get_workspaces() == {}
    assert obj.get_channels(wid=1) == {}
    assert obj._channel_migration() is False
    assert obj.send("body", "title") is False

    obj = Apprise.instantiate("twist://password:user@example.com/#Channel")
    assert isinstance(obj, NotifyTwist)

    # Calling logout on an object already logged out
    obj.logout()


@mock.patch("requests.get")
@mock.patch("requests.post")
def test_plugin_twist_cache(mock_post, mock_get):
    """NotifyTwist() Cache Handling."""

    def _response(url, *args, **kwargs):

        # Default configuration
        request = mock.Mock()
        request.status_code = requests.codes.ok
        request.content = "{}"

        if url.endswith("/login"):
            # Simulate a successful login
            request.content = dumps({
                "token": "2e82c1e4e8b0091fdaa34ff3972351821406f796",
                "default_workspace": 1,
            })

        elif url.endswith("workspaces/get"):
            request.content = dumps([
                {
                    "name": "TeamA",
                    "id": 1,
                },
                {
                    "name": "TeamB",
                    "id": 2,
                },
            ])

        elif url.endswith("channels/get"):
            request.content = dumps([
                {
                    "name": "ChanA",
                    "id": 1,
                },
                {
                    "name": "ChanB",
                    "id": 2,
                },
            ])

        return request

    mock_get.side_effect = _response
    mock_post.side_effect = _response

    # Instantiate an object
    obj = Apprise.instantiate(
        "twist://password:user@example.com/"
        "#ChanB/1:1/TeamA:ChanA/Ignore:Chan/3:1"
    )
    assert isinstance(obj, NotifyTwist)

    # Will detect channels except Ignore:Chan
    assert obj._channel_migration() is False

    # Add another channel
    obj.channels.add("ChanB")
    assert obj._channel_migration() is True

    # Nothing more to detect the second time around
    assert obj._channel_migration() is True

    # Send a notification
    assert obj.send("body", "title") is True

    def _can_not_send_response(url, *args, **kwargs):
        """Simulate a case where we can't send a notification."""
        # Force a failure
        request = mock.Mock()
        request.status_code = 403
        request.content = "{}"
        return request

    mock_get.side_effect = _can_not_send_response
    mock_post.side_effect = _can_not_send_response

    # Send a notification and fail at it
    assert obj.send("body", "title") is False


@mock.patch("requests.get")
@mock.patch("requests.post")
def test_plugin_twist_fetch(mock_post, mock_get):
    """NotifyTwist() fetch()

    fetch() is a wrapper that handles all kinds of edge cases and even attempts
    to re-authenticate to the Twist server if our token happens to expire.
    This tests these edge cases
    """

    # Track our iteration; by tracing within an object, we can re-reference
    # it within a function scope.
    _cache = {
        "first_time": True,
    }

    def _reauth_response(url, *args, **kwargs):
        """Tests re-authentication process and then a successful retry."""

        # Default configuration
        request = mock.Mock()
        request.status_code = requests.codes.ok

        # Simulate a successful login
        request.content = dumps({
            "token": "2e82c1e4e8b0091fdaa34ff3972351821406f796",
            "default_workspace": 12345,
        })

        if url.endswith("threads/add") and _cache["first_time"] is True:
            # First time iteration; act as if we failed; our second iteration
            # will not enter this and be successful. This is done by simply
            # toggling the first_time flag:
            _cache["first_time"] = False

            # otherwise, we set our first-time failure settings
            request.status_code = 403
            request.content = dumps({
                "error_code": 200,
                "error_string": "Invalid token",
            })

        return request

    mock_get.side_effect = _reauth_response
    mock_post.side_effect = _reauth_response

    # Instantiate an object
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel/34")
    assert isinstance(obj, NotifyTwist)

    # Simulate a re-authentication
    postokay, response = obj._fetch("threads/add")

    ##########################################################################
    _cache = {
        "first_time": True,
    }

    def _reauth_exception_response(url, *args, **kwargs):
        """Tests exception thrown after re-authentication process."""

        # Default configuration
        request = mock.Mock()
        request.status_code = requests.codes.ok

        # Simulate a successful login
        request.content = dumps({
            "token": "2e82c1e4e8b0091fdaa34ff3972351821406f796",
            "default_workspace": 12345,
        })

        if url.endswith("threads/add") and _cache["first_time"] is True:
            # First time iteration; act as if we failed; our second iteration
            # will not enter this and be successful. This is done by simply
            # toggling the first_time flag:
            _cache["first_time"] = False

            # otherwise, we set our first-time failure settings
            request.status_code = 403
            request.content = dumps({
                "error_code": 200,
                "error_string": "Invalid token",
            })

        elif url.endswith("threads/add") and _cache["first_time"] is False:
            # unparseable response throws the exception
            request.status_code = 200
            request.content = "{"

        return request

    mock_get.side_effect = _reauth_exception_response
    mock_post.side_effect = _reauth_exception_response

    # Instantiate an object
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel/34")
    assert isinstance(obj, NotifyTwist)

    # Simulate a re-authentication
    postokay, response = obj._fetch("threads/add")

    ##########################################################################
    _cache = {
        "first_time": True,
    }

    def _reauth_failed_response(url, *args, **kwargs):
        """Tests re-authentication process and have it not succeed."""

        # Default configuration
        request = mock.Mock()
        request.status_code = requests.codes.ok

        # Simulate a successful login
        request.content = dumps({
            "token": "2e82c1e4e8b0091fdaa34ff3972351821406f796",
            "default_workspace": 12345,
        })

        if url.endswith("threads/add") and _cache["first_time"] is True:
            # First time iteration; act as if we failed; our second iteration
            # will not enter this and be successful. This is done by simply
            # toggling the first_time flag:
            _cache["first_time"] = False

            # otherwise, we set our first-time failure settings
            request.status_code = 403
            request.content = dumps({
                "error_code": 200,
                "error_string": "Invalid token",
            })

        elif url.endswith("/login") and _cache["first_time"] is False:
            # Fail to login
            request.status_code = 403
            request.content = "{}"

        return request

    mock_get.side_effect = _reauth_failed_response
    mock_post.side_effect = _reauth_failed_response

    # Instantiate an object
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel/34")
    assert isinstance(obj, NotifyTwist)

    # Simulate a re-authentication
    postokay, response = obj._fetch("threads/add")

    def _unparseable_json_response(url, *args, **kwargs):

        # Default configuration
        request = mock.Mock()
        request.status_code = requests.codes.ok
        request.content = "{"
        return request

    mock_get.side_effect = _unparseable_json_response
    mock_post.side_effect = _unparseable_json_response

    # Instantiate our object
    obj = Apprise.instantiate("twist://password:user@example.com/#Channel/34")
    assert isinstance(obj, NotifyTwist)

    # Simulate a re-authentication
    postokay, response = obj._fetch("threads/add")
    assert postokay is True
    # When we can't parse the content, we still default to an empty
    # dictionary
    assert response == {}
