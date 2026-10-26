import React, { useRef, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Stage, Html } from '@react-three/drei';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader';
import { useLoader } from '@react-three/fiber';

class STLErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError() {
        return { hasError: true };
    }

    componentDidCatch(error, info) {
        console.error('STLViewer error:', error, info);
    }

    componentDidUpdate(prevProps) {
        if (prevProps.url !== this.props.url) {
            this.setState({ hasError: false });
        }
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="empty-state" style={{ color: '#f87171' }}>
                    Failed to load 3D model. The file may be corrupted or unavailable.
                </div>
            );
        }
        return this.props.children;
    }
}

function FallbackLoader() {
    return (
        <Html center>
            <div className="stl-fallback-loader">
                <div className="spinner"></div>
                <div>Parsing 3D Model...</div>
            </div>
        </Html>
    );
}

function Model({ url }) {
    const geom = useLoader(STLLoader, url);
    const meshRef = useRef();

    return (
        <mesh ref={meshRef} geometry={geom} rotation={[-Math.PI / 2, 0, 0]} castShadow receiveShadow>
            <meshStandardMaterial color="#58a6ff" roughness={0.3} metalness={0.8} />
        </mesh>
    );
}

export default function STLViewer({ url }) {
    if (!url) return <div className="empty-state">No model generated yet.</div>;

    return (
        <div className="viewer-container">
            <STLErrorBoundary url={url}>
            <Canvas shadows camera={{ position: [0, 50, 100], fov: 50 }}>
                <Suspense fallback={<FallbackLoader />}>
                    <Stage autoCenter preset="soft" intensity={1} environment="city">
                        <Model url={url} />
                    </Stage>
                </Suspense>
                <OrbitControls 
                    makeDefault 
                    enablePan={false} 
                />
            </Canvas>
            </STLErrorBoundary>
        </div>
    );
}
