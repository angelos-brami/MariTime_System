import { geoBounds, geoMercator, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import type { FeatureCollection, Geometry, GeoJsonProperties } from "geojson";
import type { GeometryCollection, Topology } from "topojson-specification";
import countriesData from "world-atlas/countries-110m.json";

import type { AISFeed } from "@/lib/portal-types";

type Position = AISFeed["results"][number];

const maps = [
  { corridor: "hormuz_gulf", label: "Hormuz / Gulf", bounds: [[52, 23.5], [58.5, 28.5]] },
  { corridor: "red_sea_bem_suez", label: "Red Sea / BeM / Suez", bounds: [[30, 10], [46, 32.5]] },
  { corridor: "east_med", label: "East Mediterranean", bounds: [[18, 30], [37, 42.5]] },
] as const;

const width = 560;
const height = 320;
const topology = countriesData as unknown as Topology<{ countries: GeometryCollection }>;
const land = feature(topology, topology.objects.countries) as unknown as FeatureCollection<Geometry, GeoJsonProperties>;

function CorridorPlot({
  label,
  bounds,
  positions,
}: {
  label: string;
  bounds: readonly [readonly [number, number], readonly [number, number]];
  positions: Position[];
}) {
  const clipId = `clip-${label.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`;
  const [southWest, northEast] = bounds;
  const frame = {
    type: "MultiPoint" as const,
    coordinates: [
      [southWest[0], southWest[1]],
      [northEast[0], northEast[1]],
    ],
  };
  const projection = geoMercator().fitExtent([[18, 18], [width - 18, height - 18]], frame);
  const path = geoPath(projection);
  const visibleCountries = land.features.filter((country) => {
    const [[west, south], [east, north]] = geoBounds(country);
    return east >= southWest[0] && west <= northEast[0] && north >= southWest[1] && south <= northEast[1];
  });
  return (
    <article className="aisMapPanel">
      <div className="aisMapHeading"><h2>{label}</h2><span>{positions.length} recent positions</span></div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} AIS context map with ${positions.length} positions`}>
        <desc>Latest cached vessel position broadcasts. Hollow points are stale.</desc>
        <defs><clipPath id={clipId}><rect x="18" y="18" width={width - 36} height={height - 36} /></clipPath></defs>
        <g clipPath={`url(#${clipId})`}>
          {visibleCountries.map((country, index) => <path className="aisLand" d={path(country) ?? undefined} key={index} />)}
          {positions.map((position) => {
            const point = projection([position.longitude, position.latitude]);
            if (!point) return null;
            return (
              <circle
                aria-label={`${position.vessel_name ?? position.mmsi} · ${position.age_minutes.toFixed(0)} min old`}
                className={position.stale ? "aisPoint aisPoint--stale" : "aisPoint"}
                cx={point[0]}
                cy={point[1]}
                r="4"
                key={position.id}
              />
            );
          })}
        </g>
        <rect className="aisFrame" x="18" y="18" width={width - 36} height={height - 36} />
        <text className="aisAxisLabel" x="22" y={height - 5}>{southWest[0]}°E</text>
        <text className="aisAxisLabel" x={width - 22} y={height - 5} textAnchor="end">{northEast[0]}°E</text>
      </svg>
    </article>
  );
}

export default function CorridorMap({ feed }: { feed: AISFeed }) {
  return (
    <section className="aisMapGrid" aria-label="AIS corridor context maps">
      {maps.map((map) => (
        <CorridorPlot
          bounds={map.bounds}
          key={map.corridor}
          label={map.label}
          positions={feed.results.filter((position) => position.corridor === map.corridor)}
        />
      ))}
    </section>
  );
}
