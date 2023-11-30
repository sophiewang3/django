from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, final, Generic, TypeVar
from retry import retry
from requests.exceptions import HTTPError, Timeout
import requests


SERVICE_CATALOG_BUNDLE_ENRICHMENT_LIST_URL = (
    "http://service-catalog.service-catalog.all-clusters.local-dc.fabric.dog:8080" "/api/v2/services/definitions"
)


class ServiceCatalogAPIError(Exception):
    pass


@dataclass
class ServiceCatalogEnrichmentByService:
    service_owners: dict[str, list[str]] = None
    service_custom_tags: dict[str, list[str]] = None


@dataclass
class BundleEnrichment:
    service_catalog: ServiceCatalogEnrichmentByService = None


class Bundle:
    def __init__(self, org_id, enrichment: BundleEnrichment):
        self.org_id = org_id
        self._enrichment = enrichment

    @property
    def event_worthy():
        return True
    
    @property
    def frontend_worthy():
        return True


class ServiceCatalogConfigService(object):
    @retry((ServiceCatalogAPIError, Timeout), tries=5, delay=5, backoff=1)
    def get_service_catalog_enrichment(self, org_id: int) -> ServiceCatalogEnrichmentByService:
        try:
            response = requests.get(
                SERVICE_CATALOG_BUNDLE_ENRICHMENT_LIST_URL,
                params={"orgId": org_id, "page[size]": 0},
                timeout=15,
            )
            response.raise_for_status()
            service_owners = dict()
            service_custom_tags = dict()
            for service in response.json().get("data", []):
                if service["type"] == "service-definition":
                    service_name = service["attributes"]["schema"]["dd-service"]
                    if service_name not in service_owners:
                        service_owners[service_name] = []
                    if "team" in service["attributes"]["schema"].keys():
                        team = service["attributes"]["schema"]["team"]
                    service_owners[service_name].append(team)

                    if service_name not in service_custom_tags:
                        service_custom_tags[service_name] = []
                    if "tags" in service["attributes"]["schema"].keys():
                        custom_tags = service["attributes"]["schema"]["tags"]
                    service_custom_tags[service_name].extend(custom_tags)

            for service_name, tags in service_custom_tags.items():
                service_custom_tags[service_name] = list(set(tags))

            return ServiceCatalogEnrichmentByService(service_owners, service_custom_tags)
        except HTTPError:
            raise ServiceCatalogAPIError()


T = TypeVar('T')


class BundleEnricher(Generic[T]):
    @classmethod
    def is_bundle_enricher_enabled(cls) -> bool:
        """
        Feature flag to enable bundle enrichment
        """
        return True

    @classmethod
    @final
    def name(cls) -> str:
        """e.g. FooNarrator -> foo_narrator"""
        return cls.__name__

    def enrich_bundle(self, bundle: Bundle) -> None:
        if self.should_enrich_bundle(bundle):
            enrichment = self.get_data_for_enrichment(bundle.org_id)
            self.apply_enrichment_to_bundle(bundle, enrichment)

    @abstractmethod
    def get_data_for_enrichment(self, bundle: Bundle) -> T:
        raise NotImplementedError

    @abstractmethod
    def apply_enrichment_to_bundle(self, bundle: Bundle, enrichment: T) -> None:
        raise NotImplementedError

    def should_enrich_bundle(self, bundle: Bundle) -> bool:
        return self.is_bundle_worthy(bundle) and not self.is_bundle_enriched(bundle)

    def is_bundle_worthy(self, bundle: Bundle) -> bool:
        return bundle.event_worthy() or bundle.frontend_worthy()

    @abstractmethod
    def is_bundle_enriched(self, bundle: Bundle) -> bool:
        raise NotImplementedError


@dataclass
class ServiceCatalogEnricher(BundleEnricher[ServiceCatalogEnrichmentByService]):
    def __init__(self):
        # create client for calling Service Catalog GetDefinitions API
        self.config_service: ServiceCatalogConfigService = ServiceCatalogConfigService()

    def get_data_for_enrichment(self, bundle: Bundle) -> ServiceCatalogEnrichmentByService:
        tags = [f"org_id:{bundle.org_id}", f"bundle_type:{bundle.type}"]

        return self.config_service.get_service_catalog_enrichment(bundle.org_id)

    def apply_enrichment_to_bundle(self, bundle: Bundle, enrichment: ServiceCatalogEnrichmentByService) -> None:
        bundle._enrichment.service_catalog = enrichment

    def is_bundle_enriched(self, bundle: Bundle) -> bool:
        return bundle._enrichment.service_catalog