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

from collections import OrderedDict
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from itertools import chain
import re
from xml.etree import ElementTree

import requests

from ..common import NotifyType
from ..locale import gettext_lazy as _
from ..url import PrivacyMode
from ..utils.parse import is_phone_no, parse_list, validate_regex
from .base import NotifyBase

# Topic Detection
# Summary: 256 Characters max, only alpha/numeric plus underscore (_) and
#          dash (-) additionally allowed.
#
#   Soure: https://docs.aws.amazon.com/AWSSimpleQueueService/latest\
#                   /SQSDeveloperGuide/sqs-limits.html#limits-queues
#
# Allow a starting hashtag (#) specification to help eliminate possible
# ambiguity between a topic that is comprised of all digits and a phone number
IS_TOPIC = re.compile(r"^#?(?P<name>[A-Za-z0-9_-]+)\s*$")

# Because our AWS Access Key Secret contains slashes, we actually use the
# region as a delimiter. This is a bit hacky; but it's much easier than having
# users of this product search though this Access Key Secret and escape all
# of the forward slashes!
IS_REGION = re.compile(
    r"^\s*(?P<country>[a-z]{2})-(?P<area>[a-z-]+?)-(?P<no>[0-9]+)\s*$", re.I
)

# Extend HTTP Error Messages
AWS_HTTP_ERROR_MAP = {
    403: "Unauthorized - Invalid Access/Secret Key Combination.",
}


class NotifySNS(NotifyBase):
    """A wrapper for AWS SNS (Amazon Simple Notification)"""

    # The default descriptive name associated with the Notification
    service_name = "AWS Simple Notification Service (SNS)"

    # The services URL
    service_url = "https://aws.amazon.com/sns/"

    # The default secure protocol
    secure_protocol = "sns"

    # A URL that takes you to the setup/help of the specific protocol
    setup_url = "https://github.com/caronc/apprise/wiki/Notify_sns"

    # AWS is pretty good for handling data load so request limits
    # can occur in much shorter bursts
    request_rate_per_sec = 2.5

    # The maximum length of the body
    # Source: https://docs.aws.amazon.com/sns/latest/api/API_Publish.html
    body_maxlen = 160

    # A title can not be used for SMS Messages.  Setting this to zero will
    # cause any title (if defined) to get placed into the message body.
    title_maxlen = 0

    # Define object templates
    templates = (
        "{schema}://{access_key_id}/{secret_access_key}/{region}/{targets}",
    )

    # Define our template tokens
    template_tokens = dict(
        NotifyBase.template_tokens,
        **{
            "access_key_id": {
                "name": _("Access Key ID"),
                "type": "string",
                "private": True,
                "required": True,
            },
            "secret_access_key": {
                "name": _("Secret Access Key"),
                "type": "string",
                "private": True,
                "required": True,
            },
            "region": {
                "name": _("Region"),
                "type": "string",
                "required": True,
                "regex": (r"^[a-z]{2}-[a-z-]+?-[0-9]+$", "i"),
                "map_to": "region_name",
            },
            "target_phone_no": {
                "name": _("Target Phone No"),
                "type": "string",
                "map_to": "targets",
                "regex": (r"^[0-9\s)(+-]+$", "i"),
            },
            "target_topic": {
                "name": _("Target Topic"),
                "type": "string",
                "map_to": "targets",
                "prefix": "#",
                "regex": (r"^[A-Za-z0-9_-]+$", "i"),
            },
            "targets": {
                "name": _("Targets"),
                "type": "list:string",
                "required": True,
            },
        },
    )

    # Define our template arguments
    template_args = dict(
        NotifyBase.template_args,
        **{
            "to": {
                "alias_of": "targets",
            },
            "access": {
                "alias_of": "access_key_id",
            },
            "secret": {
                "alias_of": "secret_access_key",
            },
            "region": {
                "alias_of": "region",
            },
        },
    )

    def __init__(
        self,
        access_key_id,
        secret_access_key,
        region_name,
        targets=None,
        **kwargs,
    ):
        """Initialize Notify AWS SNS Object."""
        super().__init__(**kwargs)

        # Store our AWS API Access Key
        self.aws_access_key_id = validate_regex(access_key_id)
        if not self.aws_access_key_id:
            msg = "An invalid AWS Access Key ID was specified."
            self.logger.warning(msg)
            raise TypeError(msg)

        # Store our AWS API Secret Access key
        self.aws_secret_access_key = validate_regex(secret_access_key)
        if not self.aws_secret_access_key:
            msg = (
                "An invalid AWS Secret Access Key "
                f"({secret_access_key}) was specified."
            )
            self.logger.warning(msg)
            raise TypeError(msg)

        # Acquire our AWS Region Name:
        # eg. us-east-1, cn-north-1, us-west-2, ...
        self.aws_region_name = validate_regex(
            region_name, *self.template_tokens["region"]["regex"]
        )
        if not self.aws_region_name:
            msg = f"An invalid AWS Region ({region_name}) was specified."
            self.logger.warning(msg)
            raise TypeError(msg)

        # Initialize topic list
        self.topics = []

        # Initialize numbers list
        self.phone = []

        # Set our notify_url based on our region
        self.notify_url = f"https://sns.{self.aws_region_name}.amazonaws.com/"

        # AWS Service Details
        self.aws_service_name = "sns"
        self.aws_canonical_uri = "/"

        # AWS Authentication Details
        self.aws_auth_version = "AWS4"
        self.aws_auth_algorithm = "AWS4-HMAC-SHA256"
        self.aws_auth_request = "aws4_request"

        # Validate targets and drop bad ones:
        for target in parse_list(targets):
            result = is_phone_no(target)
            if result:
                # store valid phone number in E.164 format
                self.phone.append("+{}".format(result["full"]))
                continue

            result = IS_TOPIC.match(target)
            if result:
                # store valid topic
                self.topics.append(result.group("name"))
                continue

            self.logger.warning(
                f"Dropped invalid phone/topic ({target}) specified.",
            )

        return

    def send(self, body, title="", notify_type=NotifyType.INFO, **kwargs):
        """Wrapper to send_notification since we can alert more then one
        channel."""

        if len(self.phone) == 0 and len(self.topics) == 0:
            # We have a bot token and no target(s) to message
            self.logger.warning("No AWS targets to notify.")
            return False

        # Initiaize our error tracking
        error_count = 0

        # Create a copy of our phone #'s to notify against
        phone = list(self.phone)
        topics = list(self.topics)

        while len(phone) > 0:

            # Get Phone No
            no = phone.pop(0)

            # Prepare SNS Message Payload
            payload = {
                "Action": "Publish",
                "Message": body,
                "Version": "2010-03-31",
                "PhoneNumber": no,
            }

            (result, _) = self._post(payload=payload, to=no)
            if not result:
                error_count += 1

        # Send all our defined topic id's
        while len(topics):

            # Get Topic
            topic = topics.pop(0)

            # First ensure our topic exists, if it doesn't, it gets created
            payload = {
                "Action": "CreateTopic",
                "Version": "2010-03-31",
                "Name": topic,
            }

            (result, response) = self._post(payload=payload, to=topic)
            if not result:
                error_count += 1
                continue

            # Get the Amazon Resource Name
            topic_arn = response.get("topic_arn")
            if not topic_arn:
                # Could not acquire our topic; we're done
                error_count += 1
                continue

            # Build our payload now that we know our topic_arn
            payload = {
                "Action": "Publish",
                "Version": "2010-03-31",
                "TopicArn": topic_arn,
                "Message": body,
            }

            # Send our payload to AWS
            (result, _) = self._post(payload=payload, to=topic)
            if not result:
                error_count += 1

        return error_count == 0

    def _post(self, payload, to):
        """Wrapper to request.post() to manage it's response better and make
        the send() function cleaner and easier to maintain.

        This function returns True if the _post was successful and False if it
        wasn't.
        """

        # Always call throttle before any remote server i/o is made; for AWS
        # time plays a huge factor in the headers being sent with the payload.
        # So for AWS (SNS) requests we must throttle before they're generated
        # and not directly before the i/o call like other notification
        # services do.
        self.throttle()

        # Convert our payload from a dict() into a urlencoded string
        payload = NotifySNS.urlencode(payload)

        # Prepare our Notification URL
        # Prepare our AWS Headers based on our payload
        headers = self.aws_prepare_request(payload)

        self.logger.debug(
            "AWS POST URL:"
            f" {self.notify_url} (cert_verify={self.verify_certificate!r})"
        )
        self.logger.debug(f"AWS Payload: {payload!s}")

        try:
            r = requests.post(
                self.notify_url,
                data=payload,
                headers=headers,
                verify=self.verify_certificate,
                timeout=self.request_timeout,
            )

            if r.status_code != requests.codes.ok:
                # We had a problem
                status_str = NotifySNS.http_response_code_lookup(
                    r.status_code, AWS_HTTP_ERROR_MAP
                )

                self.logger.warning(
                    "Failed to send AWS notification to {}: "
                    "{}{}error={}.".format(
                        to,
                        status_str,
                        ", " if status_str else "",
                        r.status_code,
                    )
                )

                self.logger.debug(f"Response Details:\r\n{r.content}")

                return (False, NotifySNS.aws_response_to_dict(r.text))

            else:
                self.logger.info(f'Sent AWS notification to "{to}".')

        except requests.RequestException as e:
            self.logger.warning(
                "A Connection error occurred sending AWS "
                f'notification to "{to}".',
            )
            self.logger.debug(f"Socket Exception: {e!s}")
            return (False, NotifySNS.aws_response_to_dict(None))

        return (True, NotifySNS.aws_response_to_dict(r.text))

    def aws_prepare_request(self, payload, reference=None):
        """Takes the intended payload and returns the headers for it.

        The payload is presumed to have been already urlencoded()
        """

        # Define our AWS header
        headers = {
            "User-Agent": self.app_id,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            # Populated below
            "Content-Length": 0,
            "Authorization": None,
            "X-Amz-Date": None,
        }

        # Get a reference time (used for header construction)
        reference = datetime.now(timezone.utc)

        # Provide Content-Length
        headers["Content-Length"] = str(len(payload))

        # Amazon Date Format
        amzdate = reference.strftime("%Y%m%dT%H%M%SZ")
        headers["X-Amz-Date"] = amzdate

        # Credential Scope
        scope = "{date}/{region}/{service}/{request}".format(
            date=reference.strftime("%Y%m%d"),
            region=self.aws_region_name,
            service=self.aws_service_name,
            request=self.aws_auth_request,
        )

        # Similar to headers; but a subset.  keys must be lowercase
        signed_headers = OrderedDict([
            ("content-type", headers["Content-Type"]),
            (
                "host",
                f"{self.aws_service_name}"
                f".{self.aws_region_name}.amazonaws.com",
            ),
            ("x-amz-date", headers["X-Amz-Date"]),
        ])

        #
        # Build Canonical Request Object
        #
        canonical_request = "\n".join([
            # Method
            "POST",
            # URL
            self.aws_canonical_uri,
            # Query String (none set for POST)
            "",
            # Header Content (must include \n at end!)
            # All entries except characters in amazon date must be
            # lowercase
            "\n".join([f"{k}:{v}" for k, v in signed_headers.items()]) + "\n",
            # Header Entries (in same order identified above)
            ";".join(signed_headers.keys()),
            # Payload
            sha256(payload.encode("utf-8")).hexdigest(),
        ])

        # Prepare Unsigned Signature
        to_sign = "\n".join([
            self.aws_auth_algorithm,
            amzdate,
            scope,
            sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])

        # Our Authorization header
        headers["Authorization"] = ", ".join([
            (
                f"{self.aws_auth_algorithm} "
                f"Credential={self.aws_access_key_id}/{scope}"
            ),
            "SignedHeaders={signed_headers}".format(
                signed_headers=";".join(signed_headers.keys()),
            ),
            f"Signature={self.aws_auth_signature(to_sign, reference)}",
        ])

        return headers

    def aws_auth_signature(self, to_sign, reference):
        """Generates a AWS v4 signature based on provided payload which should
        be in the form of a string."""

        def _sign(key, msg, to_hex=False):
            """Perform AWS Signing."""
            if to_hex:
                return hmac.new(key, msg.encode("utf-8"), sha256).hexdigest()
            return hmac.new(key, msg.encode("utf-8"), sha256).digest()

        _date = _sign(
            (self.aws_auth_version + self.aws_secret_access_key).encode(
                "utf-8"
            ),
            reference.strftime("%Y%m%d"),
        )

        _region = _sign(_date, self.aws_region_name)
        _service = _sign(_region, self.aws_service_name)
        _signed = _sign(_service, self.aws_auth_request)
        return _sign(_signed, to_sign, to_hex=True)

    @staticmethod
    def aws_response_to_dict(aws_response):
        """Takes an AWS Response object as input and returns it as a dictionary
        but not befor extracting out what is useful to us first.

        eg:
          IN:
            <CreateTopicResponse
                  xmlns="http://sns.amazonaws.com/doc/2010-03-31/">
              <CreateTopicResult>
                <TopicArn>arn:aws:sns:us-east-1:000000000000:abcd</TopicArn>
                   </CreateTopicResult>
               <ResponseMetadata>
               <RequestId>604bef0f-369c-50c5-a7a4-bbd474c83d6a</RequestId>
               </ResponseMetadata>
           </CreateTopicResponse>

          OUT:
           {
              type: 'CreateTopicResponse',
              request_id: '604bef0f-369c-50c5-a7a4-bbd474c83d6a',
              topic_arn: 'arn:aws:sns:us-east-1:000000000000:abcd',
           }
        """

        # Define ourselves a set of directives we want to keep if found and
        # then identify the value we want to map them to in our response
        # object
        aws_keep_map = {
            "RequestId": "request_id",
            "TopicArn": "topic_arn",
            "MessageId": "message_id",
            # Error Message Handling
            "Type": "error_type",
            "Code": "error_code",
            "Message": "error_message",
        }

        # A default response object that we'll manipulate as we pull more data
        # from our AWS Response object
        response = {
            "type": None,
            "request_id": None,
        }

        try:
            # we build our tree, but not before first eliminating any
            # reference to namespacing (if present) as it makes parsing
            # the tree so much easier.
            root = ElementTree.fromstring(
                re.sub(' xmlns="[^"]+"', "", aws_response, count=1)
            )

            # Store our response tag object name
            response["type"] = str(root.tag)

            def _xml_iter(root, response):
                if len(root) > 0:
                    for child in root:
                        # use recursion to parse everything
                        _xml_iter(child, response)

                elif root.tag in aws_keep_map:
                    response[aws_keep_map[root.tag]] = (root.text).strip()

            # Recursivly iterate over our AWS Response to extract the
            # fields we're interested in in efforts to populate our response
            # object.
            _xml_iter(root, response)

        except (ElementTree.ParseError, TypeError):
            # bad data just causes us to generate a bad response
            pass

        return response

    @property
    def url_identifier(self):
        """Returns all of the identifiers that make this URL unique from
        another simliar one.

        Targets or end points should never be identified here.
        """
        return (
            self.secure_protocol,
            self.aws_access_key_id,
            self.aws_secret_access_key,
            self.aws_region_name,
        )

    def url(self, privacy=False, *args, **kwargs):
        """Returns the URL built dynamically based on specified arguments."""

        # Our URL parameters
        params = self.url_parameters(privacy=privacy, *args, **kwargs)

        return (
            "{schema}://{key_id}/{key_secret}/{region}/{targets}/"
            "?{params}".format(
                schema=self.secure_protocol,
                key_id=self.pprint(self.aws_access_key_id, privacy, safe=""),
                key_secret=self.pprint(
                    self.aws_secret_access_key,
                    privacy,
                    mode=PrivacyMode.Secret,
                    safe="",
                ),
                region=NotifySNS.quote(self.aws_region_name, safe=""),
                targets="/".join([
                    NotifySNS.quote(x)
                    for x in chain(
                        # Phone # are already prefixed with a plus symbol
                        self.phone,
                        # Topics are prefixed with a pound/hashtag symbol
                        [f"#{x}" for x in self.topics],
                    )
                ]),
                params=NotifySNS.urlencode(params),
            )
        )

    def __len__(self):
        """Returns the number of targets associated with this notification."""
        return len(self.phone) + len(self.topics)

    @staticmethod
    def parse_url(url):
        """Parses the URL and returns enough arguments that can allow us to re-
        instantiate this object."""
        results = NotifyBase.parse_url(url, verify_host=False)
        if not results:
            # We're done early as we couldn't load the results
            return results

        # The AWS Access Key ID is stored in the hostname
        access_key_id = NotifySNS.unquote(results["host"])

        # Our AWS Access Key Secret contains slashes in it which unfortunately
        # means it is of variable length after the hostname.  Since we require
        # that the user provides the region code, we intentionally use this
        # as our delimiter to detect where our Secret is.
        secret_access_key = None
        region_name = None

        # We need to iterate over each entry in the fullpath and find our
        # region. Once we get there we stop and build our secret from our
        # accumulated data.
        secret_access_key_parts = []

        # Start with a list of entries to work with
        entries = NotifySNS.split_path(results["fullpath"])

        # Section 1: Get Region and Access Secret
        index = 0
        for i, entry in enumerate(entries):

            # Are we at the region yet?
            result = IS_REGION.match(entry)
            if result:
                # We found our Region; Rebuild our access key secret based on
                # all entries we found prior to this:
                secret_access_key = "/".join(secret_access_key_parts)

                # Ensure region is nicely formatted
                region_name = "{country}-{area}-{no}".format(
                    country=result.group("country").lower(),
                    area=result.group("area").lower(),
                    no=result.group("no"),
                )

                # Track our index as we'll use this to grab the remaining
                # content in the next Section
                index = i + 1

                # We're done with Section 1
                break

            # Store our secret parts
            secret_access_key_parts.append(entry)

        # Section 2: Get our Recipients (basically all remaining entries)
        results["targets"] = entries[index:]

        # Support the 'to' variable so that we can support rooms this way too
        # The 'to' makes it easier to use yaml configuration
        if "to" in results["qsd"] and len(results["qsd"]["to"]):
            results["targets"] += NotifySNS.parse_list(results["qsd"]["to"])

        # Handle secret_access_key over-ride
        if "secret" in results["qsd"] and len(results["qsd"]["secret"]):
            results["secret_access_key"] = NotifySNS.unquote(
                results["qsd"]["secret"]
            )
        else:
            results["secret_access_key"] = secret_access_key

        # Handle access key id over-ride
        if "access" in results["qsd"] and len(results["qsd"]["access"]):
            results["access_key_id"] = NotifySNS.unquote(
                results["qsd"]["access"]
            )
        else:
            results["access_key_id"] = access_key_id

        # Handle region name id over-ride
        if "region" in results["qsd"] and len(results["qsd"]["region"]):
            results["region_name"] = NotifySNS.unquote(
                results["qsd"]["region"]
            )
        else:
            results["region_name"] = region_name

        # Return our result set
        return results
