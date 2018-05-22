"""Classes and functions for working with Google Analytics Reporting API v4."""

import itertools
import time
import random

from apiclient.discovery import build
from apiclient.errors import HttpError
import httplib2
from oauth2client import client
from oauth2client import file
from oauth2client import tools
import pandas as pd

import easy_gar
from easy_gar.reporting.metrics import Metrics
from easy_gar.reporting.dimensions import Dimensions

__all__ = ["dimensions", "metrics", "OrderBy", "ReportingAPI"]

metrics = Metrics()
dimensions = Dimensions()


class ReportingAPI:
    """API class."""

    sampling_level = easy_gar.constants.sampling_level.default

    def __init__(self, secrets_json, view_id):
        """Init ReportingAPI object."""
        self._view_id = view_id

        # Set up a Flow object to be used if we need to authenticate.
        flow = client.flow_from_clientsecrets(
            secrets_json,
            scope=("https://www.googleapis.com/auth/analytics.readonly",),
            message=tools.message_if_missing(secrets_json),
        )

        # Prepare credentials, and authorize HTTP object with them.
        storage = file.Storage("analyticsreporting.dat")
        credentials = storage.get()
        if credentials is None or credentials.invalid:
            credentials = tools.run_flow(flow, storage)
        http = credentials.authorize(http=httplib2.Http())

        # Build the analytics reporting v4 service object.
        self._reporting = build(
            "analytics",
            "v4",
            http=http,
            discoveryServiceUrl="https://analyticsreporting.googleapis.com/"
                                "$discovery/rest",
        )

    def _request_with_exponential_backoff(self, body):
        """Return Google Analytic Reporting API v4 reponse object."""
        error = None
        for n in range(0, 5):
            try:
                return (
                    self._reporting.reports().batchGet(
                        body={"reportRequests": [body]}
                    ).execute()
                )

            except HttpError as err:
                error = err
                if err.resp.reason in [
                    "userRateLimitExceeded",
                    "quotaExceeded",
                    "internalServerError",
                    "backendError",
                ]:
                    time.sleep((2 ** n)) + random.random()
                else:
                    break

        raise error

    def _get(
        self,
        sampling_level=None,
        start_date=None,
        end_date=None,
        metrics=None,
        dimensions=None,
        order_by=None,
        page_token=None,
        page_size=None,
    ):
        """Return Google Analytics Reporing API response object."""
        request_body = {
            "samplingLevel": sampling_level or self.sampling_level,
            "viewId": self._view_id,
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "metrics": metrics,
            "dimensions": dimensions,
            "pageSize": page_size and str(page_size) or "10000",
        }
        if page_token:
            request_body["pageToken"] = str(page_token)
        if order_by:
            request_body["orderBys"] = [obj() for obj in order_by]

        # attempt request using exponential backoff
        response = self._request_with_exponential_backoff(request_body)
        return response["reports"][0]

    def get_report(
        self,
        sampling_level=None,
        start_date="7daysAgo",
        end_date="today",
        metrics=None,
        dimensions=None,
        order_by=None,
        name=None,
    ):
        """Return an API response object reporting metrics for set dates."""
        if not dimensions:
            dimensions = [easy_gar.reporting.dimensions.date]

        # Create GA metric/dimensions objects
        _metrics = [metric() for metric in metrics]
        _dimensions = [dimension() for dimension in dimensions]

        # Get initial data
        response = self._get(
            sampling_level=sampling_level,
            start_date=start_date,
            end_date=end_date,
            metrics=_metrics,
            dimensions=_dimensions,
            order_by=order_by,
        )

        if response:
            rows = (
                tuple(row["metrics"][0]["values"])
                for row in response["data"]["rows"]
            )
            indices = (
                tuple(row["dimensions"]) for row in response["data"]["rows"]
            )

            # Retrieve additional data if response is paginated
            while "nextPageToken" in response.keys():
                page_token = response["nextPageToken"]
                response = self._batch_get(
                    start_date, end_date, _metrics, _dimensions, page_token
                )
                if response:
                    rows = itertools.chain(
                        rows,
                        (
                            tuple(row["metrics"][0]["values"])
                            for row in response["data"]["rows"]
                        ),
                    )
                    indices = itertools.chain(
                        indices,
                        (
                            tuple(row["dimensions"])
                            for row in response["data"]["rows"]
                        ),
                    )

            # Set up report data (for pandas DataFrame)
            fieldnames = (metric.alias for metric in metrics)
            data = zip(fieldnames, zip(*rows))
            index = pd.MultiIndex.from_tuples(
                tuple(indices),
                names=tuple(dimension.alias for dimension in dimensions),
            )

            return easy_gar.report.Report(data, index, name)


class OrderBy:
    """Reporting API orderBy object."""

    def __init__(self, field_name=None, order_type=None, sort_order=None):
        """Init OrderBy object."""
        self.field_name = field_name
        self.order_type = order_type or easy_gar.constants.order_type.default
        self.sort_order = sort_order or easy_gar.constants.sort_order.default

    def __call__(self):
        return {
            "fieldName": str(self.field_name),
            "orderType": self.order_type,
            "sortOrder": self.sort_order,
        }