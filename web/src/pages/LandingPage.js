/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";

import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import Icon from "@cloudscape-design/components/icon";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { ExternalLinkItem } from "../common/common-components";
import "../styles/landing-page.scss";

import addPipelinesImageSrc from "../resources/img/add_pipelines.png";
import buildWorkflowsImageSrc from "../resources/img/build_workflows.png";
import uploadAndManageImageSrc from "../resources/img/upload_and_manage.png";
import visualize3dVrImageSrc from "../resources/img/visualize_3d_vr.png";

const LandingPage = (props) => {
  const { navigationOpen } = props;
  const [carouselHeight, setCarouselHeight] = useState(400);
  const firstCarouselImageEl = useRef(null);

  function handleResize() {
    const currentHeight = firstCarouselImageEl?.current?.clientHeight;
    if (currentHeight !== carouselHeight) {
      setCarouselHeight(currentHeight);
    }
  }

  setTimeout(() => handleResize(), 1000);

  window.addEventListener("resize", handleResize);

  useEffect(() => {
    handleResize();
  }, [navigationOpen, carouselHeight]);

  return (
    <Box margin={{ bottom: "l" }}>
      <div className="custom-home__header">
        <Box padding={{ vertical: "xxl", horizontal: "s" }}>
          <Grid
            gridDefinition={[
              { offset: { l: 2, xxs: 1 }, colspan: { l: 8, xxs: 10 } },
              {
                colspan: { xl: 6, l: 5, s: 6, xxs: 10 },
                offset: { l: 2, xxs: 1 },
              },
              {
                colspan: { xl: 2, l: 3, s: 4, xxs: 10 },
                offset: { s: 0, xxs: 1 },
              },
            ]}
          >
            <Box fontWeight="light" padding={{ top: "xs" }}>
              <span className="custom-home__category">
                Visual Asset Management
              </span>
            </Box>
            <div className="custom-home__header-title">
              <Box
                variant="h1"
                fontWeight="bold"
                padding="n"
                fontSize="display-l"
                color="inherit"
              >
                Amazon VAMS
              </Box>
              <Box
                fontWeight="light"
                padding={{ bottom: "s" }}
                fontSize="display-l"
                color="inherit"
              >
                Management, distribution and automation for visual assets
              </Box>
              <Box variant="p" fontWeight="light">
                <span className="custom-home__header-sub-title">
                  Visual Asset Management with Amazon VAMS is a purpose built
                  platform for storing and managing visual assets in the cloud,
                  and a plugin system which allows for customize-able
                  visualization, transformation, and delivery of these assets.
                </span>
              </Box>
            </div>
            <Container>
              <SpaceBetween size="xl">
                <Box variant="h2" padding="n">
                  Upload Assets
                </Box>
                <Box variant="p">
                  Start uploading and managing your digital assets to get
                  started.
                </Box>
                <Button href="/upload" variant="primary">
                  Upload Assets
                </Button>
              </SpaceBetween>
            </Container>
          </Grid>
        </Box>
      </div>

      <Box margin={{ top: "s" }} padding={{ top: "xxl", horizontal: "s" }}>
        <Grid
          gridDefinition={[
            {
              colspan: { xl: 6, l: 5, s: 6, xxs: 10 },
              offset: { l: 2, xxs: 1 },
            },
            {
              colspan: { xl: 2, l: 3, s: 4, xxs: 10 },
              offset: { s: 0, xxs: 1 },
            },
          ]}
        >
          <div className="custom-home-main-content-area">
            <SpaceBetween size="l">
              <div>
                <Box fontSize="heading-xl" fontWeight="normal" variant="h2">
                  How it works
                </Box>
                <Container>
                  <div className="carousel">
                    <ul
                      className="slides"
                      style={{ height: carouselHeight + "px" }}
                    >
                      <input
                        type="radio"
                        name="radio-buttons"
                        id="img-1"
                        checked
                      />
                      <li className="slide-container">
                        <div className="slide-image">
                          <img
                            ref={firstCarouselImageEl}
                            src={uploadAndManageImageSrc}
                            alt="Upload & Manage Assets"
                          />
                        </div>
                      </li>
                      <input type="radio" name="radio-buttons" id="img-2" />
                      <li className="slide-container">
                        <div className="slide-image">
                          <img
                            src={visualize3dVrImageSrc}
                            alt="Visualize in 3d & VR"
                          />
                        </div>
                      </li>
                      <input type="radio" name="radio-buttons" id="img-3" />
                      <li className="slide-container">
                        <div className="slide-image">
                          <img src={addPipelinesImageSrc} alt="Add Pipelines" />
                        </div>
                      </li>
                      <input type="radio" name="radio-buttons" id="img-4" />
                      <li className="slide-container">
                        <div className="slide-image">
                          <img
                            src={buildWorkflowsImageSrc}
                            alt="Build Workflows"
                          />
                        </div>
                      </li>
                      <div className="carousel-dots">
                        <label
                          htmlFor="img-1"
                          className="carousel-dot"
                          id="img-dot-1"
                        ></label>
                        <label
                          htmlFor="img-2"
                          className="carousel-dot"
                          id="img-dot-2"
                        ></label>
                        <label
                          htmlFor="img-3"
                          className="carousel-dot"
                          id="img-dot-3"
                        ></label>
                        <label
                          htmlFor="img-4"
                          className="carousel-dot"
                          id="img-dot-4"
                        ></label>
                      </div>
                    </ul>
                  </div>
                </Container>
              </div>

              <div>
                <Box fontSize="heading-xl" fontWeight="normal" variant="h2">
                  Benefits and features
                </Box>
                <Container>
                  <ColumnLayout columns={2} variant="text-grid">
                    <div>
                      <Box variant="h2" padding={{ top: "n" }}>
                        Secure & Serverless
                      </Box>
                      <Box variant="p">
                        A secure and serverless 3D Digital Assets Management and
                        Distribution Service
                      </Box>
                    </div>
                    <div>
                      <Box variant="h2" padding={{ top: "n" }}>
                        Simplified Solution
                      </Box>
                      <Box variant="p">
                        Offers a simplified solution for organizations to
                        ingest, store, revision control, convert, analyze, and
                        distribute digital assets (3D models, images) globally
                      </Box>
                    </div>
                    <div>
                      <Box variant="h2" padding={{ top: "n" }}>
                        Customize Asset Pipelines
                      </Box>
                      <Box variant="p">
                        Allows customers to introduce and customize asset
                        pipelines to suite their needs
                      </Box>
                    </div>
                    <div>
                      <Box variant="h2" padding={{ top: "n" }}>
                        Single Source
                      </Box>
                      <Box variant="p">
                        Enables cloud distribution of their digital assets, such
                        as animations, images, user interfaces, even packaged XR
                        tracking packages, all from a singular content delivery
                        network stored on the customers account
                      </Box>
                    </div>
                    <div>
                      <Box variant="h2" padding={{ top: "n" }}>
                        Industries
                      </Box>
                      <Box variant="p">
                        Addresses the key challenges faced by entertainment,
                        gaming, industrial augmented reality and digital twin
                        customers
                      </Box>
                    </div>
                    <div>
                      <Box variant="h2" padding={{ top: "n" }}>
                        Advanced Plugins
                      </Box>
                      <Box variant="p">
                        Offers advanced plugins for viewing, transforming and
                        analyzing digital assets
                      </Box>
                    </div>
                  </ColumnLayout>
                </Container>
              </div>
            </SpaceBetween>
          </div>

          <div className="custom-home__sidebar">
            <SpaceBetween size="xxl">
              <Container
                header={
                  <Header variant="h2">
                    Getting started{" "}
                    <span role="img" aria-label="Icon external Link">
                      <Icon name="external" />
                    </span>
                  </Header>
                }
              >
                <ul
                  aria-label="Getting started documentation"
                  className="custom-list-separator"
                >
                   <li>
                    <ExternalLinkItem
                      href="https://github.com/awslabs/visual-asset-management-system"
                      text="VAMS on Github"
                    />
                  </li>
                </ul>
              </Container>

              <Container
                header={
                  <Header variant="h2">
                    More resources{" "}
                    <span role="img" aria-label="Icon external Link">
                      <Icon name="external" />
                    </span>
                  </Header>
                }
              >
                Coming Soon
              </Container>
            </SpaceBetween>
          </div>
        </Grid>
      </Box>
    </Box>
  );
};

export default LandingPage;
