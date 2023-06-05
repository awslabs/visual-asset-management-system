jest.mock("maplibre-gl/dist/maplibre-gl", () => ({
    GeolocateControl: jest.fn(),
    Map: jest.fn(() => ({
        addControl: jest.fn(),
        on: jest.fn(),
        remove: jest.fn(),
    })),
    NavigationControl: jest.fn(),
}));
jest.mock("@aws-amplify/ui-react", () => ({}));

export default undefined;
