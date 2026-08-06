"use client";

import type { Feature, FeatureCollection, Geometry } from "geojson";
import type { GeoJSON as LeafletGeoJSON, Layer, LayerGroup, Map as LeafletMap, PathOptions } from "leaflet";
import { useEffect, useRef, useState } from "react";
import type { GeocodedLocationAnalyticsResponse, RegionAnalyticsResponse } from "@/lib/api";

type NutsProperties = {
  NUTS_ID?: string;
  CNTR_CODE?: string;
  NUTS_NAME?: string;
  NAME_LATN?: string;
};

type NutsFeature = Feature<Geometry, NutsProperties>;

type ReferenceMapLayer = {
  layer_id: string;
  title: string;
  opacity: number;
  attribution: string;
  status: string;
};

type FeatureLayer = Layer & {
  feature?: NutsFeature;
  getBounds?: () => ReturnType<LeafletGeoJSON["getBounds"]>;
  getElement?: () => Element | null;
  setStyle?: (style: PathOptions) => void;
};

function compactCurrency(value: number | string | null | undefined): string {
  const numeric = Number(value ?? 0);
  return new Intl.NumberFormat("el-GR", {
    style: "currency",
    currency: "EUR",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Number.isFinite(numeric) ? numeric : 0);
}

function regionColor(count: number, maximum: number): string {
  if (count <= 0) return "#e8efec";
  const ratio = count / Math.max(maximum, 1);
  if (ratio > 0.75) return "#0b6b53";
  if (ratio > 0.5) return "#17906d";
  if (ratio > 0.25) return "#52b993";
  return "#a9dfca";
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character] ?? character);
}

export function GreeceNutsMap({
  focusCode,
  locations,
  regions,
  onFocus,
}: {
  focusCode: string;
  locations: GeocodedLocationAnalyticsResponse[];
  regions: RegionAnalyticsResponse[];
  onFocus: (code: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const layerRef = useRef<LeafletGeoJSON | null>(null);
  const pointLayerRef = useRef<LayerGroup | null>(null);
  const focusRef = useRef(focusCode);
  const regionsRef = useRef(regions);
  const locationsRef = useRef(locations);
  const onFocusRef = useRef(onFocus);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    focusRef.current = focusCode;
    regionsRef.current = regions;
    locationsRef.current = locations;
    onFocusRef.current = onFocus;
  }, [focusCode, locations, onFocus, regions]);

  useEffect(() => {
    let cancelled = false;

    async function mountMap() {
      if (!containerRef.current || mapRef.current) return;

      try {
        const [leaflet, response] = await Promise.all([
          import("leaflet"),
          fetch("/data/nuts-2-2024.geojson"),
        ]);
        if (!response.ok) throw new Error(`NUTS GeoJSON ${response.status}`);
        const document = (await response.json()) as FeatureCollection<Geometry, NutsProperties>;
        if (cancelled || !containerRef.current) return;

        const greece: FeatureCollection<Geometry, NutsProperties> = {
          type: "FeatureCollection",
          features: document.features.filter((feature) => feature.properties?.CNTR_CODE === "EL"),
        };
        const L = leaflet.default;
        const map = L.map(containerRef.current, {
          attributionControl: true,
          minZoom: 5,
          maxZoom: 11,
          preferCanvas: false,
          scrollWheelZoom: true,
          zoomControl: false,
          zoomSnap: 0.25,
        });
        L.control.zoom({ position: "topright" }).addTo(map);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "&copy; OpenStreetMap contributors | NUTS 2024: Eurostat GISCO | Places: <a href=\"https://www.geonames.org/\">GeoNames</a>",
          maxZoom: 19,
        }).addTo(map);
        const layerControl = L.control.layers(undefined, undefined, {
          collapsed: true,
          position: "topright",
        }).addTo(map);
        try {
          const layerResponse = await fetch("/api/v1/analytics/reference-map-layers");
          if (layerResponse.ok) {
            const referenceLayers = (await layerResponse.json()) as ReferenceMapLayer[];
            referenceLayers
              .filter((layer) => layer.status === "AVAILABLE")
              .forEach((layer) => {
                const overlay = L.tileLayer.wms(
                  `/api/v1/analytics/reference-map/${encodeURIComponent(layer.layer_id)}`,
                  {
                    layers: layer.layer_id,
                    format: "image/png",
                    transparent: true,
                    opacity: layer.opacity,
                    attribution: layer.attribution,
                    version: "1.1.1",
                  },
                );
                layerControl.addOverlay(overlay, layer.title);
              });
          }
        } catch {
          // Procurement layers remain usable when an optional thematic source is down.
        }

        const styleFor = (feature?: NutsFeature): PathOptions => {
          const code = feature?.properties?.NUTS_ID ?? "";
          const stats = regionsRef.current.find((region) => region.nuts_code === code);
          const maximum = Math.max(...regionsRef.current.map((region) => region.act_count), 1);
          const active = code === focusRef.current;
          return {
            color: active ? "#17201d" : "#ffffff",
            fillColor: regionColor(stats?.act_count ?? 0, maximum),
            fillOpacity: active ? 0.92 : 0.78,
            opacity: 1,
            weight: active ? 3 : 1.25,
          };
        };

        const geoLayer = L.geoJSON(greece, {
          style: styleFor,
          onEachFeature: (feature, featureLayer) => {
            const pathLayer = featureLayer as FeatureLayer;
            const typedFeature = feature as NutsFeature;
            const code = typedFeature.properties?.NUTS_ID ?? "";
            const name = typedFeature.properties?.NUTS_NAME ?? typedFeature.properties?.NAME_LATN ?? code;

            pathLayer.on({
              add: () => pathLayer.getElement?.()?.setAttribute("data-nuts-code", code),
              click: () => onFocusRef.current(code),
              mouseout: () => geoLayer.resetStyle(pathLayer),
              mouseover: () => pathLayer.setStyle?.({ fillOpacity: 0.94, weight: 3 }),
            });
            const stats = regionsRef.current.find((region) => region.nuts_code === code);
            pathLayer.bindTooltip(
              `<strong>${name}</strong><br>${stats?.act_count ?? 0} πράξεις · ${compactCurrency(stats?.recorded_contract_value)}`,
              { direction: "top", sticky: true },
            );
          },
        }).addTo(map);

        const pointLayer = L.layerGroup().addTo(map);
        locationsRef.current.forEach((location) => {
          const marker = L.circleMarker([location.latitude, location.longitude], {
            radius: Math.min(18, 5 + Math.sqrt(location.act_count)),
            color: "#ffffff",
            fillColor: "#d97706",
            fillOpacity: 0.88,
            opacity: 1,
            weight: 1.5,
          });
          marker.bindPopup(
            `<strong>${escapeHtml(location.label)}</strong><br>` +
            `${location.opportunity_count} ευκαιρίες · ${location.contract_count} συμβάσεις<br>` +
            `${compactCurrency(location.recorded_contract_value)}`,
          );
          marker.on("click", () => {
            if (location.nuts_code?.startsWith("EL") && location.nuts_code.length >= 4) {
              onFocusRef.current(location.nuts_code.slice(0, 4));
            }
          });
          marker.addTo(pointLayer);
        });

        map.fitBounds(geoLayer.getBounds(), { padding: [16, 16] });
        mapRef.current = map;
        layerRef.current = geoLayer;
        pointLayerRef.current = pointLayer;
        setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    }

    void mountMap();
    return () => {
      cancelled = true;
      layerRef.current = null;
      pointLayerRef.current = null;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer || !mapRef.current) return;

    const maximum = Math.max(...regions.map((region) => region.act_count), 1);
    layer.setStyle((feature?: NutsFeature) => {
      const code = feature?.properties?.NUTS_ID ?? "";
      const stats = regions.find((region) => region.nuts_code === code);
      const active = code === focusCode;
      return {
        color: active ? "#17201d" : "#ffffff",
        fillColor: regionColor(stats?.act_count ?? 0, maximum),
        fillOpacity: active ? 0.92 : 0.78,
        opacity: 1,
        weight: active ? 3 : 1.25,
      };
    });

    layer.eachLayer((candidate: Layer) => {
      const featureLayer = candidate as FeatureLayer;
      const code = featureLayer.feature?.properties?.NUTS_ID;
      if (code) {
        const feature = featureLayer.feature;
        const name = feature?.properties?.NUTS_NAME ?? feature?.properties?.NAME_LATN ?? code;
        const stats = regions.find((region) => region.nuts_code === code);
        featureLayer.unbindTooltip();
        featureLayer.bindTooltip(
          `<strong>${name}</strong><br>${stats?.act_count ?? 0} πράξεις · ${compactCurrency(stats?.recorded_contract_value)}`,
          { direction: "top", sticky: true },
        );
      }
    });
  }, [focusCode, regions]);

  useEffect(() => {
    const pointLayer = pointLayerRef.current;
    if (!pointLayer) return;
    let cancelled = false;
    async function redrawPoints() {
      const leaflet = await import("leaflet");
      if (cancelled || !pointLayerRef.current) return;
      const L = leaflet.default;
      pointLayerRef.current.clearLayers();
      locations.forEach((location) => {
        const marker = L.circleMarker([location.latitude, location.longitude], {
          radius: Math.min(18, 5 + Math.sqrt(location.act_count)),
          color: "#ffffff",
          fillColor: "#d97706",
          fillOpacity: 0.88,
          opacity: 1,
          weight: 1.5,
        });
        marker.bindPopup(
          `<strong>${escapeHtml(location.label)}</strong><br>` +
          `${location.opportunity_count} ευκαιρίες · ${location.contract_count} συμβάσεις<br>` +
          `${compactCurrency(location.recorded_contract_value)}`,
        );
        marker.on("click", () => {
          if (location.nuts_code?.startsWith("EL") && location.nuts_code.length >= 4) {
            onFocusRef.current(location.nuts_code.slice(0, 4));
          }
        });
        marker.addTo(pointLayerRef.current!);
      });
    }
    void redrawPoints();
    return () => { cancelled = true; };
  }, [locations]);

  return (
    <div className="leaflet-map-shell" data-map-status={status}>
      <div
        ref={containerRef}
        className="leaflet-map-canvas"
        role="region"
        aria-label="Διαδραστικός χάρτης περιφερειών και τόπων εκτέλεσης διαγωνισμών στην Ελλάδα"
        data-testid="greece-nuts-map"
        onClickCapture={(event) => {
          const target = event.target instanceof Element ? event.target.closest("[data-nuts-code]") : null;
          const code = target?.getAttribute("data-nuts-code");
          if (code) onFocus(code);
        }}
      />
      {status === "loading" && <div className="map-loading">Φόρτωση γεωγραφικών ορίων…</div>}
      {status === "error" && <div className="map-loading map-error">Δεν φορτώθηκαν τα όρια NUTS.</div>}
    </div>
  );
}
